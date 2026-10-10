#!/usr/bin/env python3
"""Frozen constants and shared helpers for the #79 owner agreement study (owner-vbt-agreement-79-v1).

Every value here restates docs/plans/VBT_AGREEMENT_PREREGISTRATION.md (frozen content 75bc5f1) or a
verified freeze reference (see docs/validation/VBT_AGREEMENT_STUDY_TOOLS.md). Changing any of them is
a protocol amendment and needs a new study identity, never an in-place edit after outcomes.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import analyze_lift as workflow

ROOT = workflow.ROOT
STUDY_ID = "owner-vbt-agreement-79-v1"
TOOL_VERSION = 1

# Freeze references.
PREREGISTRATION_COMMIT = "75bc5f16853987469b76b1996894ec220726ff2d"
PREREGISTRATION_MAIN_COMMIT = "c6a58b5"
FREEZE_INSTANT = datetime.fromisoformat("2026-10-09T17:20:54+02:00")
OPENBAR_BASELINE_COMMIT = "742225dc21d0d947db0ddf472218cd4dc442dc24"
OPENBAR_CLI_SHA256 = "8561b1c1e1f9099e8a53b3ece1578192d73a94c5c2f0d5c80e7a7f0d6d1d7cc6"
CONSUMER_COMMIT = "a74cb9dca24864af9f348bf9853fb6fe4f475521"
NOVELTY_EXCLUSION_SHA256 = "2c8cbc0275b11b94a5e89494d0190ec9a121d7c43c38fdd0679a2f26176894ea"

# Cohort: three sessions, per session SQ1, SQ2, SN1, SN2, CL1, CL2; three attempted reps each.
SESSIONS = (1, 2, 3)
LIFTS: dict[str, dict[str, Any]] = {
    "SQ": {"exercise": "back_squat", "load_kg": 40},
    "SN": {"exercise": "snatch", "load_kg": 30},
    "CL": {"exercise": "clean", "load_kg": 40},
}
SLOTS = tuple(f"S{session}-{code}-{index}" for session in SESSIONS for code in LIFTS for index in (1, 2))
PLANNED_REPS_PER_VIDEO = 3
VIDEOS_PER_LIFT = len(SESSIONS) * 2

# Frozen measurement method.
TRACKER = "csrt"
TRACKER_IMPLEMENTATION = "opencv-csrt"
TRACKER_POLICY = "csrt-all-v1"
PRESET = "vbt-sg-0.15s-v1"
ANALYZE_OPTIONS = ["--filter", "savitzky-golay", "--filter-window-s", "0.15", "--filter-polynomial-order", "2",
                   "--filter-max-gap-s", "0.2", "--kinematics-max-gap-s", "0.2", "--kinematics-min-confidence", "0"]
STICK_LENGTH_M = 1.30

# Frozen consumer contract and decision rule.
SEGMENTATION = "concentric-segmentation-v2"
MIN_OVERLAP = 0.5
WL_PARSER = "wl-analysis-csv-v2"
OPENBAR_PARSER = "openbar-analysis-v2"
REPORT_SCHEMA = "velocity-agreement-v1"
TOLERANCE_MPS = 0.05

# Private study layout inside the pinned worktree (git-ignored).
STUDY_DIR = Path("validation/private/vbt/study-79")
SESSIONS_DIR = Path("validation/private/vbt/sessions")
EXCLUSION_LIST = STUDY_DIR / "novelty-exclusion-v1.sha256-list.txt"

FAILURE_STAGES = ("recording", "transfer", "novelty", "confirmation", "processing", "assessment",
                  "reference", "wl_export", "protocol")

# Consumer report metrics: primary first, then secondary (velocityAgreement.ts METRICS).
METRICS = ("meanVelocityMps", "peakVelocityMps", "romCm")

# The preregistered hand-check slot and its private output format.
HANDCHECK_SLOT = "S1-SQ-1"
HANDCHECK_FORMAT = "owner-vbt-agreement-79-handcheck"

COMMIT_RE = re.compile(r"[0-9a-f]{40}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")


class StudyInputError(ValueError):
    """Invalid or inconsistent study input (exit 1)."""


class StudyInfrastructureError(RuntimeError):
    """Tool or environment unavailable; nothing written, retry unchanged inputs (exit 3)."""


def git_state(root: Path) -> tuple[str, bool]:
    """HEAD commit of a checkout and whether its tracked files are clean."""
    git = ["git", "-C", str(root)]
    try:
        head = subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, check=False)
        porcelain = subprocess.run([*git, "status", "--porcelain", "--untracked-files=no"],
                                   capture_output=True, check=False)
    except OSError as error:
        raise StudyInfrastructureError(f"git could not be run: {error}") from error
    commit = head.stdout.decode("utf-8", errors="replace").strip()
    if head.returncode != 0 or porcelain.returncode != 0 or COMMIT_RE.fullmatch(commit) is None:
        raise StudyInfrastructureError(f"cannot read the git state of {root}")
    return commit, porcelain.stdout.strip() == b""


# Private study documents shared by the inventory, hand-check and report tools.
SLOTS_FORMAT = "owner-vbt-agreement-79-slots"
INVENTORY_FORMAT = "owner-vbt-agreement-79-inventory"
LOCK_SUMMARY_FORMAT = "owner-vbt-agreement-79-lock-summary"
STUDY_DOCUMENT_VERSION = 1


def freeze_references() -> dict[str, Any]:
    """Every frozen constant, as recorded in study outputs (deterministic, JSON-ready)."""
    return {
        "study_id": STUDY_ID, "preregistration_commit": PREREGISTRATION_COMMIT,
        "preregistration_main_commit": PREREGISTRATION_MAIN_COMMIT, "freeze_instant": FREEZE_INSTANT.isoformat(),
        "openbar_baseline_commit": OPENBAR_BASELINE_COMMIT, "openbar_cli_sha256": OPENBAR_CLI_SHA256,
        "consumer_commit": CONSUMER_COMMIT, "novelty_exclusion_sha256": NOVELTY_EXCLUSION_SHA256,
        "slots": list(SLOTS), "lifts": LIFTS, "planned_reps_per_video": PLANNED_REPS_PER_VIDEO,
        "tracker": TRACKER, "tracker_implementation": TRACKER_IMPLEMENTATION, "tracker_policy": TRACKER_POLICY,
        "preset": PRESET, "analyze_options": list(ANALYZE_OPTIONS), "stick_length_m": STICK_LENGTH_M,
        "segmentation": SEGMENTATION, "min_overlap": MIN_OVERLAP, "wl_parser": WL_PARSER,
        "openbar_parser": OPENBAR_PARSER, "report_schema": REPORT_SCHEMA, "tolerance_mps": TOLERANCE_MPS,
    }


def repo_relative(path: Path, root: Path) -> str:
    """Forward-slash path of ``path`` relative to ``root`` (``workflow.display_path`` for any root)."""
    return workflow.safe_resolve(path).relative_to(workflow.safe_resolve(root)).as_posix()


def recorded_path(root: Path, value: object) -> Path | None:
    """Resolve a canonical repository-relative POSIX path recorded in evidence; None when it is not one.

    Mirrors ``assess_clip.recorded_repo_path`` for an explicit root: absolute, escaping or non-canonical
    spellings (``./``, ``..``, backslashes) are refused instead of being normalised.
    """
    if not isinstance(value, str) or not value or "\0" in value or "\\" in value or Path(value).is_absolute():
        return None
    try:
        resolved = workflow.safe_resolve(root / value)
    except workflow.WorkflowError:
        return None
    if not workflow.is_within(resolved, root) or repo_relative(resolved, root) != value:
        return None
    return resolved


def slot_lift(slot: str) -> dict[str, Any]:
    """Lift metadata for a frozen slot id such as ``S1-SQ-1``."""
    code = slot.split("-")[1]
    return {"code": code, **LIFTS[code]}


def slot_session(slot: str) -> int:
    return int(slot.split("-")[0][1:])


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def serialize(value: Any) -> bytes:
    """Deterministic UTF-8 JSON with LF endings; no clock, host or random values."""
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def load_json(path: Path) -> Any:
    """Strict JSON (duplicate keys and non-finite numbers rejected) from exact UTF-8 bytes."""
    return workflow.schema_check.loads_strict(path.read_bytes().decode("utf-8"))


def write_new(path: Path, data: bytes) -> None:
    """Write a new file; never overwrite retained evidence."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(data)


def js_round(value: float, scale: int) -> float:
    """ECMAScript ``Math.round(value * scale) / scale``: nearest integer, ties toward +infinity."""
    scaled = value * scale
    if not math.isfinite(scaled):
        raise ValueError("non-finite value cannot be rounded")
    floor = math.floor(scaled)
    return (floor + 1 if scaled - floor >= 0.5 else floor) / scale
