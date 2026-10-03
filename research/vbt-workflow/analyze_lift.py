#!/usr/bin/env python3
"""Personal post-session VBT workflow (#86): lift video + manual seed -> analysis-v1.

A thin orchestrator with no measurement logic. It chains existing tools:

  register  hash the video and add it to a separate personal manifest
            (default validation/private/vbt/manifest.json) with id `vbt-<first 16 hex of SHA-256>`,
            through validation/tools/fixture_probe.py. Idempotent: the same video gives the same id
            and an identical entry is never rewritten.
  run       register (idempotent), then run research/opencv-tracking/track.py with CSRT over the
            whole clip, then `openbar-cli analyze --observations` with an explicit filter and
            kinematics configuration. Writes the prediction, analysis-v1 and a run record side by side.

Measurement stays where it lives: CSRT in track.py; calibration, filtering and kinematics in
openbar-core through `analyze`. This script never edits a prediction or an analysis.

It never writes the #57 development/held-out manifest (validation/private/manifest.json) or the
public manifest. Standard library only; run it with the research venv interpreter because
track.py needs OpenCV. See docs/plans/VBT_WORKFLOW_PLAN.md (step 2) for the owner flow.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "tools"))
import fixture_probe  # noqa: E402
import schema_check  # noqa: E402

WORKFLOW_VERSION = "vbt-workflow-1"
RUN_RECORD_FORMAT = "openbar-research-vbt-run-record"
RUN_RECORD_FORMAT_VERSION = 1

DEFAULT_MANIFEST = ROOT / "validation" / "private" / "vbt" / "manifest.json"
PROTECTED_MANIFEST = ROOT / "validation" / "private" / "manifest.json"
DEFAULT_SEED_DIR = ROOT / "validation" / "private" / "vbt" / "seeds"
LABEL_WORK_DIR = ROOT / "validation" / "private" / "annotations" / "work"
SEED_SCHEMA = ROOT / "validation" / "schema" / "manual-target-seed-v1.schema.json"
TRACK_SCRIPT = ROOT / "research" / "opencv-tracking" / "track.py"

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

    def capture(self, argv: list[str]) -> str:
        try:
            completed = subprocess.run(
                self._resolve(argv), cwd=ROOT, check=False, capture_output=True,
                text=True, encoding="utf-8", errors="replace",
            )
        except OSError as error:
            raise WorkflowError(f"{argv[0]} could not be run: {error}") from error
        if completed.returncode != 0:
            raise WorkflowError(f"{argv[0]} failed: {completed.stderr.strip()[-500:]}")
        return completed.stdout

    def execute(self, argv: list[str]) -> None:
        try:
            completed = subprocess.run(self._resolve(argv), cwd=ROOT, check=False)
        except OSError as error:
            raise WorkflowError(f"{argv[0]} could not be run: {error}") from error
        if completed.returncode != 0:
            raise WorkflowError(f"{argv[0]} exited with status {completed.returncode}")

    @staticmethod
    def _resolve(argv: list[str]) -> list[str]:
        # A relative program path is resolved against the repository root, not the caller's cwd.
        program = Path(argv[0])
        if not program.is_absolute() and len(program.parts) > 1:
            return [str(ROOT / program), *argv[1:]]
        return argv


def fixture_id_for(sha256: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
        raise WorkflowError(f"not a SHA-256 digest: {sha256!r}")
    return FIXTURE_ID_PREFIX + sha256.lower()[:FIXTURE_ID_HEX_DIGITS]


def display_path(path: Path) -> str:
    """Repository-relative forward-slash path, or an absolute one outside the repository."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def canonical_sha256(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    return fixture_probe.compute_sha256(path)


def require_personal_manifest(path: Path) -> Path:
    resolved = path.resolve()
    if resolved == PROTECTED_MANIFEST.resolve():
        raise WorkflowError(
            f"refusing to use {display_path(PROTECTED_MANIFEST)}: it is the #57 development/held-out "
            "manifest and personal videos would contaminate tracker-selection evidence; "
            f"use a separate manifest such as {display_path(DEFAULT_MANIFEST)}"
        )
    if fixture_probe.is_public_manifest(resolved):
        raise WorkflowError(f"refusing to register personal videos in the public fixture tree: {path}")
    return path


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


def load_manifest_fixtures(manifest: Path) -> list[dict[str, Any]]:
    if not manifest.is_file():
        return []
    try:
        document = schema_check.load_strict(manifest)
    except schema_check.DocumentError as error:
        raise WorkflowError(str(error)) from error
    fixtures = document.get("fixtures") if isinstance(document, dict) else None
    if not isinstance(fixtures, list):
        raise WorkflowError(f"{manifest} has no fixtures list")
    return fixtures


def register_video(video: Path, manifest: Path, sha256: str, plate_diameter_m: float,
                   exercise: str) -> tuple[dict[str, Any], str]:
    """Return the manifest entry for `video` and whether it was `added` or `reused`.

    Same video -> same id. An existing entry whose binding fields match is reused byte-for-byte;
    a conflicting one fails closed rather than being overwritten.
    """
    fixture_id = fixture_id_for(sha256)
    drafted = fixture_probe.draft_fixture_manifest(
        video, fixture_id=fixture_id, exercise=exercise, purpose=PURPOSE,
        plate_diameter_m=plate_diameter_m, challenge_tags=[REGISTRATION_TAG], notes=REGISTRATION_NOTES,
    )
    if drafted["media"]["sha256"].lower() != sha256.lower():
        raise WorkflowError(f"{video} changed while it was being registered")
    fixtures = load_manifest_fixtures(manifest)
    for fixture in fixtures:
        same_media = str(fixture.get("media", {}).get("sha256", "")).lower() == sha256.lower()
        if same_media and fixture.get("id") != fixture_id:
            raise WorkflowError(
                f"{video} is already registered in {manifest} as '{fixture.get('id')}'; "
                f"remove that entry or keep using it outside this workflow (expected id '{fixture_id}')"
            )
    existing = next((fixture for fixture in fixtures if fixture.get("id") == fixture_id), None)
    if existing is None:
        fixture_probe.append_to_manifest(manifest, drafted)
        return drafted, "added"
    conflicts = [field for field in BINDING_FIELDS if existing.get(field) != drafted[field]]
    if conflicts:
        raise WorkflowError(
            f"'{fixture_id}' in {manifest} was registered with a different {', '.join(conflicts)}; "
            "fix or remove that entry deliberately (for example a wrong --plate-diameter-m or --exercise)"
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


def first_line(text: str) -> str:
    return text.strip().splitlines()[0] if text.strip() else ""


def git_provenance(runner: Runner) -> dict[str, Any]:
    try:
        commit = runner.capture(["git", "-C", str(ROOT), "rev-parse", "HEAD"]).strip()
        status = runner.capture(["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=no"])
    except WorkflowError as error:
        raise WorkflowError(f"cannot record the OpenBar git commit: {error}") from error
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise WorkflowError(f"git rev-parse returned an unexpected commit {commit!r}")
    return {"git_commit": commit, "tracked_changes": bool(status.strip())}


def tool_versions(runner: Runner, openbar_cli: str | None) -> dict[str, Any]:
    versions: dict[str, Any] = {
        "python": platform.python_version(),
        "ffmpeg": first_line(runner.capture(["ffmpeg", "-version"])),
        "ffprobe": first_line(runner.capture(["ffprobe", "-version"])),
    }
    if openbar_cli is None:
        versions["cargo"] = first_line(runner.capture(["cargo", "--version"]))
        versions["rustc"] = first_line(runner.capture(["rustc", "--version"]))
    else:
        binary = Path(openbar_cli) if Path(openbar_cli).is_absolute() else ROOT / openbar_cli
        versions["openbar_cli_sha256"] = file_sha256(binary) if binary.is_file() else None
    return versions


def track_command(manifest: Path, fixture_id: str, seed: Path, prediction: Path) -> list[str]:
    return [
        sys.executable, display_path(TRACK_SCRIPT),
        "--manifest", display_path(manifest), "--fixture", fixture_id, "--seed", display_path(seed),
        "--tracker", TRACKER, "--output", display_path(prediction),
    ]


def analyze_command(openbar_cli: str | None, manifest: Path, fixture_id: str, seed: Path, plate: str,
                    prediction: Path, options: list[str], analysis: Path) -> list[str]:
    prefix = CARGO_ANALYZE if openbar_cli is None else [openbar_cli]
    return [
        *prefix, "analyze",
        "--manifest", display_path(manifest), "--fixture", fixture_id, "--seed", display_path(seed),
        "--plate-diameter-m", plate, "--observations", display_path(prediction),
        *options, "--output", display_path(analysis),
    ]


def write_json(path: Path, document: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(document, indent=2) + "\n")


def remove_outputs(paths: dict[str, Path]) -> None:
    for path in paths.values():
        path.unlink(missing_ok=True)


def run_steps(runner: Runner, commands: list[tuple[str, list[str]]]) -> None:
    for step, argv in commands:
        print(f"[{step}] {' '.join(argv)}", flush=True)
        try:
            runner.execute(argv)
        except WorkflowError as error:
            raise WorkflowError(f"{step} step failed: {error}") from error


def build_run_record(*, fixture_id: str, video: Path, video_sha256: str, seed: Path,
                     seed_document: dict[str, Any], manifest: Path, entry: dict[str, Any],
                     args: argparse.Namespace, plate: float, options: list[str],
                     commands: list[tuple[str, list[str]]], paths: dict[str, Path],
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
                "sha256": file_sha256(seed),
                "fixture_id": seed_document["fixture_id"],
                "timestamp_s": seed_document["seed"]["timestamp_s"],
                "frame_index": seed_document["seed"].get("frame_index"),
            },
            "manifest": {"path": display_path(manifest)},
            "manifest_entry": {"sha256": canonical_sha256(entry)},
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
            name: {"file": paths[name].name, "sha256": file_sha256(paths[name])}
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
    print(f"  prediction: {display_path(paths['prediction'])}")
    print(f"  analysis:   {display_path(paths['analysis'])}")
    print(f"  run record: {display_path(paths['run_record'])}")
    print(f"  OpenBar commit: {git['git_commit']}")
    if git["tracked_changes"]:
        print("  WARNING: the OpenBar working tree has uncommitted changes to tracked files; "
              "the commit alone does not reproduce this run.")


def command_register(args: argparse.Namespace, runner: Runner) -> int:
    manifest = require_personal_manifest(args.manifest)
    plate = parse_plate_diameter(args.plate_diameter_m)
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


def command_run(args: argparse.Namespace, runner: Runner) -> int:
    manifest = require_personal_manifest(args.manifest)
    plate = parse_plate_diameter(args.plate_diameter_m)
    options = analysis_options(args)
    require_tools(runner, ("ffmpeg", "ffprobe"))

    video_sha256 = file_sha256(args.video)
    fixture_id = fixture_id_for(video_sha256)
    paths = output_paths(args.output_dir, fixture_id)
    existing = [path for path in paths.values() if path.exists()]
    if existing and not args.force:
        raise WorkflowError(
            "output already exists (pass --force to replace): " + ", ".join(display_path(p) for p in existing)
        )
    seed_document = load_bound_seed(args.seed, fixture_id, args.video)
    git = git_provenance(runner)
    versions = tool_versions(runner, args.openbar_cli)
    entry, action = register_video(args.video, manifest, video_sha256, plate, args.exercise)

    commands = [
        ("track", track_command(manifest, fixture_id, args.seed, paths["prediction"])),
        ("analyze", analyze_command(args.openbar_cli, manifest, fixture_id, args.seed, args.plate_diameter_m,
                                    paths["prediction"], options, paths["analysis"])),
    ]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    remove_outputs(paths)
    try:
        run_steps(runner, commands)
        try:
            prediction = schema_check.load_strict(paths["prediction"])
        except (schema_check.DocumentError, OSError) as error:
            raise WorkflowError(f"cannot read the tracker prediction: {error}") from error
        record = build_run_record(
            fixture_id=fixture_id, video=args.video, video_sha256=video_sha256, seed=args.seed,
            seed_document=seed_document, manifest=manifest, entry=entry, args=args, plate=plate,
            options=options, commands=commands, paths=paths, prediction=prediction, git=git, versions=versions,
        )
        write_json(paths["run_record"], record)
    except BaseException:
        remove_outputs(paths)
        raise
    print_summary(fixture_id, args.exercise, action, seed_document, prediction, paths, git)
    return 0


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--video", type=Path, required=True, help="lift video inside the repository")
    parser.add_argument("--plate-diameter-m", required=True, help="plate diameter in metres (no default)")
    parser.add_argument("--exercise", required=True, choices=EXERCISES)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST,
                        help=f"personal manifest (default: {display_path(DEFAULT_MANIFEST)})")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_common(subparsers.add_parser("register", help="add the video to the personal manifest (idempotent)"))

    run = subparsers.add_parser("run", help="register, track with CSRT, analyze")
    add_common(run)
    run.add_argument("--seed", type=Path, required=True, help="manual-target-seed-v1 made for this video")
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--force", action="store_true", help="replace existing outputs for this video")
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
