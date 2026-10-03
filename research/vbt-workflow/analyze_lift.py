#!/usr/bin/env python3
"""Personal post-session VBT workflow (#86): lift video + manual seed -> analysis-v1.

A thin orchestrator with no measurement logic. It chains existing tools:

  register  hash the video and add it to a separate personal manifest
            (default validation/private/vbt/manifest.json) with id `vbt-<first 16 hex of SHA-256>`,
            through validation/tools/fixture_probe.py. Idempotent: the same video gives the same id
            and an identical entry is never rewritten.
  run       register (idempotent), then run research/opencv-tracking/track.py --omit-runtime with
            CSRT over the whole clip, then `openbar-cli analyze --observations` with an explicit
            filter and kinematics configuration. Writes the prediction, analysis-v1 and a run record
            side by side; they replace earlier outputs only when the whole run succeeds.

Measurement stays where it lives: CSRT in track.py; calibration, filtering and kinematics in
openbar-core through `analyze`. This script never edits a prediction or an analysis.

Manifests are accepted only under validation/private/vbt/ or target/, and never one named
validation/private/manifest.json (the #57 development/held-out manifest). Standard library only;
run it with the research venv interpreter because track.py needs OpenCV.
See docs/plans/VBT_WORKFLOW_PLAN.md (step 2) for the owner flow.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "tools"))
import fixture_probe  # noqa: E402
import schema_check  # noqa: E402

WORKFLOW_VERSION = "vbt-workflow-2"
RUN_RECORD_FORMAT = "openbar-research-vbt-run-record"
RUN_RECORD_FORMAT_VERSION = 1

PERSONAL_ROOT = ROOT / "validation" / "private" / "vbt"
TARGET_ROOT = ROOT / "target"
ALLOWED_ROOTS = (PERSONAL_ROOT, TARGET_ROOT)
DEFAULT_MANIFEST = PERSONAL_ROOT / "manifest.json"
PERSONAL_MEDIA_DIR = PERSONAL_ROOT / "media"
DEFAULT_SEED_DIR = PERSONAL_ROOT / "seeds"
PROTECTED_MANIFEST_TAIL = ("validation", "private", "manifest.json")
LABEL_WORK_DIR = ROOT / "validation" / "private" / "annotations" / "work"
SEED_SCHEMA = ROOT / "validation" / "schema" / "manual-target-seed-v1.schema.json"
PREDICTION_SCHEMA = ROOT / "validation" / "schema" / "tracker-prediction-v1.schema.json"
ANALYSIS_SCHEMA = ROOT / "validation" / "schema" / "analysis-v1.schema.json"
TRACK_SCRIPT = ROOT / "research" / "opencv-tracking" / "track.py"
UNTRACKED_SOURCE_DIRS = ("crates", "apps", "research")

FIXTURE_ID_PREFIX = "vbt-"
FIXTURE_ID_HEX_DIGITS = 16
PURPOSE = "development"
REGISTRATION_TAG = "personal-vbt"
REGISTRATION_NOTES = (
    "Personal VBT session registered by research/vbt-workflow/analyze_lift.py (#86); "
    "not #57 tracker-selection evidence."
)
# Fields that bind an id to its media and metric scale. Conditions and notes may be hand-edited.
BINDING_FIELDS = ("exercise", "media", "video", "load")

TRACKER = "csrt"
TRACKER_IMPLEMENTATION = "opencv-csrt"
CARGO_ANALYZE = ["cargo", "run", "--locked", "--release", "-p", "openbar-cli", "--"]
EXERCISES = ("snatch", "clean", "back_squat", "other")
FILTERS = ("raw", "moving-average", "savitzky-golay", "kalman")
OUTPUT_NAMES = ("prediction", "analysis", "run_record")  # promotion order: the run record last
RENAME_ATTEMPTS = 5
RENAME_RETRY_SLEEP_S = 0.2

# Named presets expand to explicit analyze flags, which the run record stores verbatim.
PRESETS: dict[str, list[str]] = {
    "vbt-sg-0.15s-v1": [
        "--filter", "savitzky-golay",
        "--filter-window-s", "0.15",
        "--filter-polynomial-order", "2",
        "--filter-max-gap-s", "0.2",
        "--kinematics-max-gap-s", "0.2",
        "--kinematics-min-confidence", "0",
    ],
}


class WorkflowError(RuntimeError):
    pass


def finite_number(text: str) -> str:
    """argparse type: a finite number, kept as the exact string passed on to analyze."""
    try:
        value = float(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number") from error
    if not math.isfinite(value):
        raise argparse.ArgumentTypeError(f"{text!r} is not finite")
    return text


def whole_number(text: str) -> str:
    """argparse type: a non-negative integer, kept as the exact string passed on to analyze."""
    if not text.isdigit():
        raise argparse.ArgumentTypeError(f"{text!r} is not a non-negative integer")
    return text


# Filter flags forwarded to analyze in this fixed order; analyze rejects irrelevant or missing ones.
FILTER_FLAGS: tuple[tuple[str, Any], ...] = (
    ("--filter-window", whole_number),
    ("--filter-window-s", finite_number),
    ("--filter-polynomial-order", whole_number),
    ("--filter-max-gap-s", finite_number),
    ("--filter-acceleration-variance-m2-s4", finite_number),
    ("--filter-measurement-variance-m2", finite_number),
    ("--filter-initial-velocity-variance-m2-s2", finite_number),
    ("--filter-confidence-window-samples", whole_number),
)
KINEMATICS_FLAGS = ("--kinematics-max-gap-s", "--kinematics-min-confidence")


def dest(flag: str) -> str:
    return flag.lstrip("-").replace("-", "_")


class Runner:
    """External process boundary; tests substitute a fake."""

    def which(self, name: str) -> str | None:
        return shutil.which(name)

    def capture_bytes(self, argv: list[str]) -> bytes:
        try:
            completed = subprocess.run(self._resolve(argv), cwd=ROOT, check=False, capture_output=True)
        except OSError as error:
            raise WorkflowError(f"{argv[0]} could not be run: {error}") from error
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            raise WorkflowError(f"{argv[0]} failed: {stderr[-500:]}")
        return completed.stdout

    def capture(self, argv: list[str]) -> str:
        return self.capture_bytes(argv).decode("utf-8", errors="replace")

    def succeeds(self, argv: list[str]) -> bool:
        try:
            return subprocess.run(self._resolve(argv), cwd=ROOT, check=False, capture_output=True).returncode == 0
        except OSError:
            return False

    def execute(self, argv: list[str]) -> None:
        try:
            completed = subprocess.run(self._resolve(argv), cwd=ROOT, check=False)
        except OSError as error:
            raise WorkflowError(f"{argv[0]} could not be run: {error}") from error
        if completed.returncode != 0:
            raise WorkflowError(f"{argv[0]} exited with status {completed.returncode}")

    @staticmethod
    def _resolve(argv: list[str]) -> list[str]:
        # A repository-relative program path is resolved against the root, not the caller's cwd.
        program = Path(argv[0])
        if not program.is_absolute() and (ROOT / program).is_file():
            return [str(ROOT / program), *argv[1:]]
        return argv


# --- Paths and locations -------------------------------------------------------------------------

def safe_resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except (OSError, RuntimeError) as error:
        raise WorkflowError(f"cannot resolve {path}: {error}") from error


def is_within(path: Path, root: Path) -> bool:
    try:
        safe_resolve(path).relative_to(safe_resolve(root))
    except ValueError:
        return False
    return True


def display_path(path: Path) -> str:
    """Forward-slash path relative to the repository root (callers ensure it is inside)."""
    resolved = safe_resolve(path)
    try:
        return resolved.relative_to(safe_resolve(ROOT)).as_posix()
    except ValueError:
        return resolved.as_posix()


def require_in_repo(path: Path, flag: str, hint: str) -> Path:
    if not is_within(path, ROOT):
        raise WorkflowError(f"{flag} {path} is outside the repository; {hint}")
    return path


def normalized_tail(text: str, count: int = 3) -> tuple[str, ...]:
    """Last path parts, case-folded, without trailing dots/spaces or an NTFS `:stream` suffix."""
    parts = [part for part in re.split(r"[\\/]+", text) if part]
    return tuple(part.split(":", 1)[0].rstrip(". ").lower() for part in parts[-count:])


def names_protected_manifest(path: Path) -> bool:
    spellings = [str(path), str(path.absolute())]
    try:
        spellings.append(str(path.resolve()))
    except (OSError, RuntimeError):
        pass  # an unresolvable spelling is refused by the allow-list instead
    return any(normalized_tail(spelling) == PROTECTED_MANIFEST_TAIL for spelling in spellings)


def require_personal_manifest(path: Path) -> Path:
    if names_protected_manifest(path):
        raise WorkflowError(
            f"refusing to use {path}: validation/private/manifest.json is the #57 development/held-out "
            "manifest and personal videos would contaminate tracker-selection evidence; "
            f"use {display_path(DEFAULT_MANIFEST)}"
        )
    if not any(is_within(path, root) for root in ALLOWED_ROOTS):
        raise WorkflowError(
            f"refusing to use manifest {path}: personal manifests must be under validation/private/vbt/ or target/"
        )
    return path


def require_output_dir(path: Path, runner: Runner, paths: dict[str, Path]) -> None:
    require_in_repo(path, "--output-dir", "write under validation/private/vbt/analyses/")
    if any(is_within(path, root) for root in ALLOWED_ROOTS):
        return
    check_paths = [*paths.values(), *staged_paths(paths).values()]
    ignored = not is_within(path, fixture_probe.PUBLIC_FIXTURES_DIR) and all(
        runner.succeeds(["git", "-C", str(ROOT), "check-ignore", "-q", display_path(output)])
        for output in check_paths
    )
    if not ignored:
        raise WorkflowError(
            f"refusing --output-dir {path}: final and staging outputs must be git-ignored, "
            "or under validation/private/vbt/ or target/"
        )


def resolve_openbar_cli(value: str) -> Path:
    """Resolve like the other path flags (caller's cwd); `shutil.which` adds PATHEXT such as .exe."""
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    # A bare name plus an explicit search path makes `which` apply PATHEXT on Windows (Python 3.11
    # skips it for names with a directory). Windows also searches the cwd first, so check the hit.
    found = shutil.which(candidate.name, path=str(candidate.parent))
    if found is None or safe_resolve(Path(found).parent) != safe_resolve(candidate.parent):
        raise WorkflowError(f"--openbar-cli {value} was not found or is not executable")
    binary = Path(found)
    require_in_repo(binary, "--openbar-cli", "use target/release/openbar-cli from `cargo build --locked --release`")
    return binary


# --- Inputs --------------------------------------------------------------------------------------

def fixture_id_for(sha256: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
        raise WorkflowError(f"not a SHA-256 digest: {sha256!r}")
    return FIXTURE_ID_PREFIX + sha256.lower()[:FIXTURE_ID_HEX_DIGITS]


def canonical_sha256(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    return fixture_probe.compute_sha256(path)


def require_tools(runner: Runner, names: tuple[str, ...]) -> None:
    missing = [name for name in names if runner.which(name) is None]
    if missing:
        raise WorkflowError(f"{' and '.join(missing)} not found on PATH; install FFmpeg (ADR-0006) first")


def parse_plate_diameter(text: str) -> float:
    try:
        value = float(text)
    except ValueError as error:
        raise WorkflowError(f"--plate-diameter-m must be a number, got {text!r}") from error
    if not math.isfinite(value) or value <= 0:
        raise WorkflowError(f"--plate-diameter-m must be a positive finite number of metres, got {text!r}")
    return value


def require_video(video: Path) -> Path:
    return require_in_repo(video, "--video", f"copy it under {display_path(PERSONAL_MEDIA_DIR)}/ first")


def load_manifest_fixtures(manifest: Path) -> list[dict[str, Any]]:
    if not manifest.is_file():
        return []
    try:
        document = schema_check.load_strict(manifest)
        fixture_probe.validate_manifest(document)
    except (schema_check.DocumentError, fixture_probe.ProbeError) as error:
        raise WorkflowError(f"personal manifest {display_path(manifest)} is invalid: {error}") from error
    return document["fixtures"]


def describe_conflicts(existing: dict[str, Any], drafted: dict[str, Any], fields: list[str]) -> str:
    return "; ".join(
        f"{field}: registered {json.dumps(existing.get(field), sort_keys=True)}, "
        f"this run {json.dumps(drafted[field], sort_keys=True)}"
        for field in fields
    )


def draft_entry(video: Path, fixture_id: str, plate_diameter_m: float, exercise: str) -> dict[str, Any]:
    try:
        return fixture_probe.draft_fixture_manifest(
            video, fixture_id=fixture_id, exercise=exercise, purpose=PURPOSE,
            plate_diameter_m=plate_diameter_m, challenge_tags=[REGISTRATION_TAG], notes=REGISTRATION_NOTES,
        )
    except fixture_probe.ProbeError as error:
        if "outside the repository" in str(error):
            raise WorkflowError(
                f"{video} is outside the repository; copy it under {display_path(PERSONAL_MEDIA_DIR)}/ first"
            ) from error
        raise WorkflowError(f"cannot register {video}: {error}") from error


def register_video(video: Path, manifest: Path, sha256: str, plate_diameter_m: float,
                   exercise: str) -> tuple[dict[str, Any], str]:
    """Return the manifest entry for `video` and whether it was `added` or `reused`.

    Same video -> same id. An existing entry whose binding fields match is reused byte-for-byte;
    a conflicting one fails closed rather than being overwritten.
    """
    fixture_id = fixture_id_for(sha256)
    drafted = draft_entry(video, fixture_id, plate_diameter_m, exercise)
    if drafted["media"]["sha256"].lower() != sha256.lower():
        raise WorkflowError(f"{video} changed while it was being registered")
    fixtures = load_manifest_fixtures(manifest)
    for fixture in fixtures:
        same_media = str(fixture.get("media", {}).get("sha256", "")).lower() == sha256.lower()
        if same_media and fixture.get("id") != fixture_id:
            raise WorkflowError(
                f"{video} is already registered in {display_path(manifest)} as '{fixture.get('id')}'; "
                f"remove that entry or keep using it outside this workflow (expected id '{fixture_id}')"
            )
    existing = next((fixture for fixture in fixtures if fixture.get("id") == fixture_id), None)
    if existing is None:
        fixture_probe.append_to_manifest(manifest, drafted)
        return drafted, "added"
    conflicts = [field for field in BINDING_FIELDS if existing.get(field) != drafted[field]]
    if conflicts:
        raise WorkflowError(
            f"'{fixture_id}' in {display_path(manifest)} conflicts with this run "
            f"({describe_conflicts(existing, drafted, conflicts)}); fix or remove that entry deliberately"
        )
    return existing, "reused"


def load_bound_seed(seed_path: Path, fixture_id: str, video: Path) -> dict[str, Any]:
    """Load a manual-target-seed-v1 and require it to belong to this exact video."""
    try:
        document = schema_check.load_strict(seed_path)
    except (schema_check.DocumentError, OSError) as error:
        raise WorkflowError(f"cannot read seed: {error}") from error
    if not isinstance(document, dict) or "fixture_id" not in document:
        raise WorkflowError(
            f"seed {seed_path} has no fixture_id, so it cannot be bound to {video.name}; create it with "
            "`annotations.py seed --manifest <personal manifest>` after `analyze_lift.py register`"
        )
    if document["fixture_id"] != fixture_id:
        raise WorkflowError(
            f"seed is for a different video: its fixture_id is '{document['fixture_id']}', but {video.name} "
            f"has id '{fixture_id}' (from its SHA-256)"
        )
    errors = schema_check.validate_document(document, schema_check.load_schema(SEED_SCHEMA))
    if errors:
        raise WorkflowError(f"seed {seed_path} is not a valid manual-target-seed-v1:\n  " + "\n  ".join(errors))
    return document


def load_validated_output(path: Path, schema_path: Path, label: str) -> dict[str, Any]:
    """Load a generated JSON artifact and require it to satisfy its committed wire schema."""
    try:
        document = schema_check.load_strict(path)
        schema = schema_check.load_schema(schema_path)
        errors = schema_check.validate_document(document, schema)
    except (schema_check.DocumentError, schema_check.SchemaError, OSError) as error:
        raise WorkflowError(f"cannot validate {label}: {error}") from error
    if errors:
        raise WorkflowError(f"{label} does not match {schema_path.name}:\n  " + "\n  ".join(errors))
    if not isinstance(document, dict):
        raise WorkflowError(f"{label} must be a JSON object")
    return document


def require_inputs_unchanged(*, video: Path, video_sha256: str, seed: Path, seed_sha256: str,
                             manifest: Path, fixture_id: str, entry_sha256: str) -> None:
    """Fail before promotion if an input used by the external steps changed during the run."""
    if file_sha256(video) != video_sha256:
        raise WorkflowError("video changed while the workflow was running; staged outputs were not promoted")
    if file_sha256(seed) != seed_sha256:
        raise WorkflowError("seed changed while the workflow was running; staged outputs were not promoted")
    current = next(
        (fixture for fixture in load_manifest_fixtures(manifest) if fixture.get("id") == fixture_id),
        None,
    )
    if current is None or canonical_sha256(current) != entry_sha256:
        raise WorkflowError("manifest entry changed while the workflow was running; staged outputs were not promoted")


def analysis_options(args: argparse.Namespace) -> list[str]:
    if args.preset is not None:
        return list(PRESETS[args.preset])
    options = ["--filter", args.filter]
    for flag, _ in FILTER_FLAGS:
        value = getattr(args, dest(flag))
        if value is not None:
            options += [flag, value]
    for flag in KINEMATICS_FLAGS:
        options += [flag, getattr(args, dest(flag))]
    return options


def output_paths(output_dir: Path, fixture_id: str) -> dict[str, Path]:
    return {
        "prediction": output_dir / f"{fixture_id}.{TRACKER_IMPLEMENTATION}.prediction-v1.json",
        "analysis": output_dir / f"{fixture_id}.analysis-v1.json",
        "run_record": output_dir / f"{fixture_id}.run-record.json",
    }


def staged_paths(paths: dict[str, Path]) -> dict[str, Path]:
    return {name: path.with_name(f".{path.name}.tmp") for name, path in paths.items()}


# --- Provenance ----------------------------------------------------------------------------------

def first_line(text: str) -> str:
    return text.strip().splitlines()[0] if text.strip() else ""


def git_provenance(runner: Runner) -> dict[str, Any]:
    git = ["git", "-C", str(ROOT)]
    try:
        commit = runner.capture([*git, "rev-parse", "HEAD"]).strip()
        diff = runner.capture_bytes([*git, "diff", "HEAD", "--binary", "--no-ext-diff", "--no-textconv",
                                     "--no-color"])
        untracked = runner.capture([*git, "ls-files", "--others", "--exclude-standard", "--",
                                    *UNTRACKED_SOURCE_DIRS])
    except WorkflowError as error:
        raise WorkflowError(f"cannot record the OpenBar git state: {error}") from error
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise WorkflowError(f"git rev-parse returned an unexpected commit {commit!r}")
    return {
        "git_commit": commit,
        "tracked_changes": bool(diff),
        "tracked_diff_sha256": hashlib.sha256(diff).hexdigest(),
        "untracked_source_files": len([line for line in untracked.splitlines() if line.strip()]),
    }


def tool_versions(runner: Runner, openbar_cli: Path | None) -> dict[str, Any]:
    versions: dict[str, Any] = {
        "python": platform.python_version(),
        "ffmpeg": first_line(runner.capture(["ffmpeg", "-version"])),
        "ffprobe": first_line(runner.capture(["ffprobe", "-version"])),
    }
    if openbar_cli is None:
        versions["cargo"] = first_line(runner.capture(["cargo", "--version"]))
        versions["rustc"] = first_line(runner.capture(["rustc", "--version"]))
    else:
        versions["openbar_cli"] = {"path": display_path(openbar_cli), "sha256": file_sha256(openbar_cli)}
    return versions


# --- Commands ------------------------------------------------------------------------------------

def track_command(manifest: Path, fixture_id: str, seed: Path, prediction: Path) -> list[str]:
    return [
        sys.executable, display_path(TRACK_SCRIPT),
        "--manifest", display_path(manifest), "--fixture", fixture_id, "--seed", display_path(seed),
        "--tracker", TRACKER, "--omit-runtime", "--output", display_path(prediction),
    ]


def analyze_command(openbar_cli: Path | None, manifest: Path, fixture_id: str, seed: Path, plate: str,
                    prediction: Path, options: list[str], analysis: Path) -> list[str]:
    prefix = CARGO_ANALYZE if openbar_cli is None else [display_path(openbar_cli)]
    return [
        *prefix, "analyze",
        "--manifest", display_path(manifest), "--fixture", fixture_id, "--seed", display_path(seed),
        "--plate-diameter-m", plate, "--observations", display_path(prediction),
        *options, "--output", display_path(analysis),
    ]


def write_json(path: Path, document: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(document, indent=2) + "\n")


def remove_files(paths: dict[str, Path]) -> None:
    for path in paths.values():
        path.unlink(missing_ok=True)


def cleanup_staged(staged: dict[str, Path]) -> None:
    """Best-effort removal of staging files; problems are reported, never raised over a real error."""
    for path in staged.values():
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            print(f"warning: could not remove staging file {display_path(path)}: {error}", file=sys.stderr)


def retry_on_permission_error(action: Any, describe: str) -> None:
    """Windows readers/antivirus can hold a file briefly; retry a fixed number of times."""
    for attempt in range(1, RENAME_ATTEMPTS + 1):
        try:
            action()
            return
        except PermissionError as error:
            if attempt == RENAME_ATTEMPTS:
                raise WorkflowError(
                    f"could not {describe} after {RENAME_ATTEMPTS} attempts: {error}; the output set is "
                    "incomplete (there is no run record), so re-run with --force"
                ) from error
            time.sleep(RENAME_RETRY_SLEEP_S)


def promote_outputs(staged: dict[str, Path], paths: dict[str, Path]) -> None:
    """Move staged outputs into place; the run record goes first-out and last-in.

    Renames are not atomic as a set. Removing the old run record first and renaming the new one
    last means an interrupted promotion always leaves no run record: no record = incomplete set.
    """
    record = paths["run_record"]
    retry_on_permission_error(lambda: record.unlink(missing_ok=True), f"remove {display_path(record)}")
    for name in OUTPUT_NAMES:
        source, target = staged[name], paths[name]
        retry_on_permission_error(lambda: os.replace(source, target), f"move {display_path(target)} into place")


def run_steps(runner: Runner, commands: list[tuple[str, list[str]]]) -> None:
    for step, argv in commands:
        print(f"[{step}] {' '.join(argv)}", flush=True)
        try:
            runner.execute(argv)
        except WorkflowError as error:
            raise WorkflowError(f"{step} step failed: {error}") from error


def build_run_record(*, fixture_id: str, video: Path, video_sha256: str, seed: Path,
                     seed_sha256: str, seed_document: dict[str, Any], manifest: Path,
                     entry_sha256: str, args: argparse.Namespace,
                     plate: float, options: list[str], commands: list[tuple[str, list[str]]],
                     paths: dict[str, Path], staged: dict[str, Path],
                     prediction: dict[str, Any], git: dict[str, Any],
                     versions: dict[str, Any]) -> dict[str, Any]:
    config = prediction.get("implementation", {}).get("config", {})
    recorded_commands = [
        {"step": step, "argv": ["python", *argv[1:]] if step == "track" else argv}
        for step, argv in commands
    ]
    return {
        "format": RUN_RECORD_FORMAT,
        "format_version": RUN_RECORD_FORMAT_VERSION,
        "workflow_version": WORKFLOW_VERSION,
        "fixture_id": fixture_id,
        "inputs": {
            "video": {"path": display_path(video), "sha256": video_sha256},
            "seed": {
                "path": display_path(seed),
                "sha256": seed_sha256,
                "fixture_id": seed_document["fixture_id"],
                "timestamp_s": seed_document["seed"]["timestamp_s"],
                "frame_index": seed_document["seed"].get("frame_index"),
            },
            "manifest": {"path": display_path(manifest)},
            "manifest_entry": {"sha256": entry_sha256},
        },
        "configuration": {
            "exercise": args.exercise,
            "plate_diameter_m": plate,
            "tracker": TRACKER,
            "tracker_implementation": TRACKER_IMPLEMENTATION,
            "preset": args.preset,
            "analyze_options": options,
        },
        "commands": recorded_commands,
        "commands_note": "Run from the repository root; 'python' is the research venv interpreter.",
        "outputs": {
            name: {"file": paths[name].name, "sha256": file_sha256(staged[name])}
            for name in ("prediction", "analysis")
        },
        "openbar": git,
        "environment": {
            **versions,
            "opencv_version": config.get("opencv_version"),
            "numpy_version": config.get("numpy_version"),
        },
    }


def print_summary(fixture_id: str, exercise: str, action: str, seed_document: dict[str, Any],
                  prediction: dict[str, Any], paths: dict[str, Path], git: dict[str, Any]) -> None:
    seed = seed_document["seed"]
    samples = prediction.get("samples", [])
    tracked = sum(sample.get("state") == "tracked" for sample in samples)
    lost = sum(sample.get("state") == "lost" for sample in samples)
    print()
    print(f"analyze_lift: {fixture_id} ({exercise}); manifest entry {action}")
    print(f"  SEED: {float(seed['timestamp_s']):.6f} s, frame {seed.get('frame_index')}. Tracking starts at the "
          "seed, so it must be before the first rep; check this before using the analysis.")
    print(f"  samples: {tracked} tracked, {lost} lost, {len(samples)} total")
    for label, name in (("prediction", "prediction"), ("analysis  ", "analysis"), ("run record", "run_record")):
        print(f"  {label}: {display_path(paths[name])}")
    print(f"  OpenBar commit: {git['git_commit']}")
    if git["tracked_changes"] or git["untracked_source_files"]:
        print("  WARNING: the OpenBar working tree has uncommitted or untracked source changes; "
              "the commit alone does not reproduce this run (see the run record).")


# --- Subcommands ---------------------------------------------------------------------------------

def command_register(args: argparse.Namespace, runner: Runner) -> int:
    manifest = require_personal_manifest(args.manifest)
    plate = parse_plate_diameter(args.plate_diameter_m)
    require_video(args.video)
    require_tools(runner, ("ffprobe",))
    sha256 = file_sha256(args.video)
    entry, action = register_video(args.video, manifest, sha256, plate, args.exercise)
    fixture_id = entry["id"]
    package = LABEL_WORK_DIR / f"{fixture_id}.seed"
    seed = DEFAULT_SEED_DIR / f"{fixture_id}.manual-target-seed-v1.json"
    state = "added" if action == "added" else "already registered, unchanged"
    print(f"{fixture_id}: {state} in {display_path(manifest)}")
    print("Next, make the seed on a frame BEFORE the first rep (tracking runs forward from it):")
    print(f"  python validation/tools/label_package.py --manifest {display_path(manifest)} --fixture {fixture_id} "
          f"--at-s <seconds-before-first-rep> --annotator-id seed")
    print(f"  # open {display_path(package)}/index.html: click the plate centre, Shift+click the rim, "
          "press 1/2/3 for quality, download the CSV")
    print(f"  python validation/tools/annotations.py seed --manifest {display_path(manifest)} "
          f"--metadata {display_path(package)}/metadata.json --csv <downloaded.csv> --output {display_path(seed)}")
    print("Then run this script's `run` subcommand with --seed pointing at that file.")
    return 0


def check_run_inputs(args: argparse.Namespace, runner: Runner) -> tuple[Path, float, list[str], Path | None]:
    manifest = require_personal_manifest(args.manifest)
    plate = parse_plate_diameter(args.plate_diameter_m)
    options = analysis_options(args)
    require_video(args.video)
    require_in_repo(args.seed, "--seed", f"keep seeds under {display_path(DEFAULT_SEED_DIR)}/")
    openbar_cli = None if args.openbar_cli is None else resolve_openbar_cli(args.openbar_cli)
    require_tools(runner, ("ffmpeg", "ffprobe"))
    return manifest, plate, options, openbar_cli


def command_run(args: argparse.Namespace, runner: Runner) -> int:
    manifest, plate, options, openbar_cli = check_run_inputs(args, runner)
    video_sha256 = file_sha256(args.video)
    fixture_id = fixture_id_for(video_sha256)
    paths = output_paths(args.output_dir, fixture_id)
    require_output_dir(args.output_dir, runner, paths)
    existing = [path for path in paths.values() if path.exists()]
    if existing and not args.force:
        raise WorkflowError(
            "output already exists (pass --force to replace): " + ", ".join(display_path(p) for p in existing)
        )
    seed_sha256 = file_sha256(args.seed)
    seed_document = load_bound_seed(args.seed, fixture_id, args.video)
    if file_sha256(args.seed) != seed_sha256:
        raise WorkflowError("seed changed while it was being read; re-run with a stable seed file")
    git = git_provenance(runner)
    versions = tool_versions(runner, openbar_cli)
    entry, action = register_video(args.video, manifest, video_sha256, plate, args.exercise)
    entry_sha256 = canonical_sha256(entry)

    staged = staged_paths(paths)

    def commands_for(files: dict[str, Path]) -> list[tuple[str, list[str]]]:
        return [
            ("track", track_command(manifest, fixture_id, args.seed, files["prediction"])),
            ("analyze", analyze_command(openbar_cli, manifest, fixture_id, args.seed, args.plate_diameter_m,
                                        files["prediction"], options, files["analysis"])),
        ]

    commands = commands_for(staged)  # what runs: staged .tmp outputs
    recorded = commands_for(paths)  # what is recorded: final names (no output embeds its path)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    remove_files(staged)  # leftovers of an interrupted run; never the real outputs
    try:
        run_steps(runner, commands)
        require_inputs_unchanged(
            video=args.video, video_sha256=video_sha256, seed=args.seed, seed_sha256=seed_sha256,
            manifest=manifest, fixture_id=fixture_id, entry_sha256=entry_sha256,
        )
        prediction = load_validated_output(staged["prediction"], PREDICTION_SCHEMA, "tracker prediction")
        load_validated_output(staged["analysis"], ANALYSIS_SCHEMA, "analysis-v1")
        record = build_run_record(
            fixture_id=fixture_id, video=args.video, video_sha256=video_sha256, seed=args.seed,
            seed_sha256=seed_sha256, seed_document=seed_document, manifest=manifest,
            entry_sha256=entry_sha256, args=args, plate=plate, options=options, commands=recorded,
            paths=paths, staged=staged, prediction=prediction, git=git, versions=versions,
        )
        write_json(staged["run_record"], record)
        promote_outputs(staged, paths)
    finally:
        cleanup_staged(staged)
    print_summary(fixture_id, args.exercise, action, seed_document, prediction, paths, git)
    return 0


# --- CLI -----------------------------------------------------------------------------------------

def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--video", type=Path, required=True, help="lift video inside the repository")
    parser.add_argument("--plate-diameter-m", required=True, help="plate diameter in metres (no default)")
    parser.add_argument("--exercise", required=True, choices=EXERCISES)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST,
                        help="personal manifest under validation/private/vbt/ or target/ "
                             "(default: validation/private/vbt/manifest.json)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_common(subparsers.add_parser("register", help="add the video to the personal manifest (idempotent)"))

    run = subparsers.add_parser("run", help="register, track with CSRT, analyze")
    add_common(run)
    run.add_argument("--seed", type=Path, required=True, help="manual-target-seed-v1 made for this video")
    run.add_argument("--output-dir", type=Path, required=True,
                     help="git-ignored directory, e.g. validation/private/vbt/analyses")
    run.add_argument("--force", action="store_true", help="replace existing outputs once the new run succeeds")
    run.add_argument("--openbar-cli", help="prebuilt openbar-cli binary instead of `cargo run --locked --release`")
    run.add_argument("--preset", choices=sorted(PRESETS), help="named analyze configuration; excludes explicit flags")
    run.add_argument("--filter", choices=FILTERS)
    for flag, kind in FILTER_FLAGS:
        run.add_argument(flag, type=kind)
    for flag in KINEMATICS_FLAGS:
        run.add_argument(flag, type=finite_number)
    return parser


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command != "run":
        return args
    explicit = [flag for flag in ("--filter", *(f for f, _ in FILTER_FLAGS), *KINEMATICS_FLAGS)
                if getattr(args, dest(flag)) is not None]
    if args.preset is not None and explicit:
        parser.error(f"--preset cannot be combined with {', '.join(explicit)}")
    if args.preset is None:
        missing = [flag for flag in ("--filter", *KINEMATICS_FLAGS) if getattr(args, dest(flag)) is None]
        if missing:
            parser.error(f"either --preset or explicit {', '.join(missing)} is required (no silent defaults)")
    return args


def main(argv: list[str] | None = None, runner: Runner | None = None) -> int:
    args = parse_args(argv)
    runner = runner or Runner()
    try:
        if args.command == "register":
            return command_register(args, runner)
        return command_run(args, runner)
    except (WorkflowError, fixture_probe.ProbeError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
