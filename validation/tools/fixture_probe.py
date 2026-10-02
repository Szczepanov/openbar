#!/usr/bin/env python3
"""Media probe, contact-sheet, and fixture manifest drafting tool for OpenBar.

Provides deterministic inspection of raw video clips, contact sheet rendering,
and schema-compliant drafting for validation/development fixture manifests.

Standard library only. FFmpeg/ffprobe must be on PATH for media operations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path
from typing import Any

import schema_check

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_SCHEMA_PATH = ROOT / "validation" / "schema" / "fixture-manifest-v1.schema.json"
PUBLIC_FIXTURES_DIR = ROOT / "validation" / "fixtures" / "public"

# Stream- and format-level sections only. A bare section name such as `tags` also matches
# packet and frame sections, which makes ffprobe read every packet and decode every frame.
PROBE_ENTRIES = (
    "stream=index,width,height,sample_aspect_ratio,avg_frame_rate,r_frame_rate,nb_frames,duration"
    ":stream_side_data=rotation"
    ":format=duration"
)


class ProbeError(RuntimeError):
    pass


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    """Execute external command with error handling."""
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as error:
        raise ProbeError(f"{command[0]} is not on PATH") from error
    if completed.returncode != 0:
        raise ProbeError(f"{command[0]} failed: {completed.stderr.strip()[-500:]}")
    return completed


def compute_sha256(path: Path) -> str:
    """Compute SHA-256 digest of a file in chunks."""
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def display_size(width_px: int, height_px: int, rotation_deg: int) -> tuple[int, int]:
    """Display-oriented size: 90/270 degree rotation swaps encoded width and height."""
    return (height_px, width_px) if rotation_deg % 180 == 90 else (width_px, height_px)


def parse_rotation(stream: dict[str, Any]) -> int:
    """Display-matrix rotation in degrees, normalised to 0/90/180/270 like `media::probe`.

    The legacy `rotate` tag is deliberately ignored, as in the Rust probe: it uses the
    opposite (clockwise) sign convention, and FFmpeg converts it to a display matrix on demux.
    """
    rotation = next(
        (
            side["rotation"]
            for side in stream.get("side_data_list", [])
            if isinstance(side, dict) and "rotation" in side
        ),
        None,
    )
    if rotation is None:
        return 0

    try:
        rotation_val = float(rotation)
    except (TypeError, ValueError) as error:
        raise ProbeError(f"unsupported rotation {rotation!r}") from error

    if not math.isfinite(rotation_val) or not rotation_val.is_integer() or abs(rotation_val) > 360:
        raise ProbeError(f"invalid rotation {rotation}")

    deg = int(rotation_val) % 360
    if deg not in (0, 90, 180, 270):
        raise ProbeError(f"rotation {deg} is not a multiple of 90 degrees")
    return deg


def validate_sample_aspect_ratio(value: str | None) -> None:
    """Reject non-square pixels, which the Rust frame source (`media::probe`) also rejects."""
    # "0:1" is FFmpeg's "unknown", which it treats as square pixels.
    if value not in (None, "1:1", "0:1"):
        raise ProbeError(f"unsupported sample aspect ratio {value!r}; only square pixels are supported")


def parse_fps(rate_str: str | None) -> float | None:
    """Safely parse FFmpeg rational frame rate string like '30/1' or '29340000/489503'."""
    if not rate_str or rate_str == "0/0":
        return None
    try:
        frac = Fraction(rate_str)
        if frac <= 0:
            return None
        return float(frac)
    except (ValueError, ZeroDivisionError):
        return None


def parse_duration(value: Any) -> float | None:
    """Positive finite duration in seconds, or None when absent or unusable."""
    try:
        duration_s = float(value)
    except (TypeError, ValueError):
        return None
    return duration_s if math.isfinite(duration_s) and duration_s > 0 else None


def nominal_fps_for(ref_fps: float) -> int:
    """Snap a measured rate to the standard nominal rate it was recorded at (e.g. 29.97 -> 30)."""
    if abs(ref_fps - 29.97) < 0.5 or abs(ref_fps - 30.0) < 0.5:
        return 30
    if abs(ref_fps - 59.94) < 1.0 or abs(ref_fps - 60.0) < 1.0:
        return 60
    if abs(ref_fps - 119.88) < 1.0 or abs(ref_fps - 120.0) < 1.0:
        return 120
    if abs(ref_fps - 240.0) < 2.0:
        return 240
    return round(ref_fps)


def repository_relative_path(path: Path) -> str | None:
    """Forward-slash path relative to the repository root, or None for files outside it."""
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return None


def probe_video(path: Path) -> dict[str, Any]:
    """Probe video file using ffprobe and calculate SHA-256."""
    if not path.is_file():
        raise ProbeError(f"file not found: {path}")

    file_sha256 = compute_sha256(path)

    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", PROBE_ENTRIES, "-of", "json", str(path)]
    completed = run_command(cmd)

    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ProbeError(f"ffprobe returned invalid JSON: {error}") from error

    streams = data.get("streams") or []
    if not streams:
        raise ProbeError(f"no video stream found in {path}")
    stream = streams[0]
    fmt = data.get("format") or {}

    width_px = stream.get("width")
    height_px = stream.get("height")
    if not isinstance(width_px, int) or width_px <= 0 or not isinstance(height_px, int) or height_px <= 0:
        raise ProbeError(f"invalid video dimensions {width_px}x{height_px}")

    validate_sample_aspect_ratio(stream.get("sample_aspect_ratio"))
    rotation_deg = parse_rotation(stream)
    dw_px, dh_px = display_size(width_px, height_px, rotation_deg)

    # The video stream's own duration; the container duration can include a longer audio track.
    duration_s = parse_duration(stream.get("duration")) or parse_duration(fmt.get("duration"))
    if duration_s is None:
        raise ProbeError("could not determine video duration")

    nb_frames_str = stream.get("nb_frames")
    nb_frames = int(nb_frames_str) if nb_frames_str and nb_frames_str.isdigit() else None

    # avg_frame_rate is ffprobe's frame count over the video stream duration; it stays unknown
    # (None) rather than being substituted when the container does not report it.
    avg_fps = parse_fps(stream.get("avg_frame_rate"))
    ref_fps = avg_fps or parse_fps(stream.get("r_frame_rate"))
    if ref_fps is None:
        raise ProbeError("could not determine video frame rate")

    return {
        "repository_path": repository_relative_path(path),
        "sha256": file_sha256,
        "duration_s": round(duration_s, 6),
        "encoded_width_px": width_px,
        "encoded_height_px": height_px,
        "rotation_deg": rotation_deg,
        "display_width_px": dw_px,
        "display_height_px": dh_px,
        "nominal_fps": nominal_fps_for(ref_fps),
        "measured_fps": round(avg_fps, 2) if avg_fps else None,
        "frame_count": nb_frames,
    }


def contact_sheet_timestamps(duration_s: float, total_frames: int) -> list[float]:
    """Evenly spaced target times strictly inside the clip."""
    step_s = duration_s / (total_frames + 1)
    return [round(step_s * (i + 1), 3) for i in range(total_frames)]


def contact_sheet_select_expr(timestamps: list[float]) -> str:
    """`select` expression keeping exactly the first frame at or after each target time.

    A fixed time window around each target would keep several frames at high frame rates
    (duplicate tiles) and none at low ones.
    """
    terms = "+".join(
        f"gte(t\\,{t:.3f})*(isnan(prev_selected_t)+lt(prev_selected_t\\,{t:.3f}))" for t in timestamps
    )
    return f"gt({terms}\\,0)"


def generate_contact_sheet(
    path: Path,
    output_path: Path,
    rows: int = 3,
    cols: int = 4,
    thumb_width_px: int = 320,
) -> Path:
    """Generate a multi-frame contact sheet grid using ffmpeg."""
    if not path.is_file():
        raise ProbeError(f"media file not found: {path}")
    if rows <= 0 or cols <= 0:
        raise ProbeError("rows and cols must be positive integers")

    total_frames = rows * cols
    output_path.parent.mkdir(parents=True, exist_ok=True)

    info = probe_video(path)
    timestamps = contact_sheet_timestamps(info["duration_s"], total_frames)
    filter_chain = (
        f"select='{contact_sheet_select_expr(timestamps)}',"
        f"scale={thumb_width_px}:-1,"
        f"tile={cols}x{rows}:nb_frames={total_frames}:padding=4:color=black"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-nostdin",
        "-hide_banner",
        "-autorotate",
        "-i",
        str(path),
        "-vf",
        filter_chain,
        "-vframes",
        "1",
        "-q:v",
        "3",
        str(output_path),
    ]
    run_command(cmd)

    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise ProbeError(f"failed to generate contact sheet at {output_path}")
    return output_path


def draft_fixture_manifest(
    path: Path,
    *,
    fixture_id: str,
    exercise: str,
    purpose: str = "validation",
    plate_diameter_m: float = 0.45,
    camera_view: str = "side",
    camera_movement: str = "fixed",
    lighting: str = "good",
    plate_visibility: str = "clear",
    occlusion: str = "none",
    motion_blur: str = "moderate",
    challenge_tags: list[str] | None = None,
    source_kind: str = "self_recorded",
    rights_holder: str | None = "Project owner",
    notes: str = "",
) -> dict[str, Any]:
    """Draft a single fixture manifest item conforming to fixture-manifest-v1.schema.json.

    Video fields come from the probe. Camera, conditions, load and source default to a typical
    self-recorded side-view clip; override any that do not fit the recording.
    """
    if not math.isfinite(plate_diameter_m) or plate_diameter_m <= 0:
        raise ProbeError(f"plate diameter must be a positive finite number of metres, got {plate_diameter_m}")

    info = probe_video(path)
    if info["repository_path"] is None:
        raise ProbeError(f"{path} is outside the repository; move it under validation/private/media first")

    tags = set(challenge_tags or [])
    if info["rotation_deg"] != 0:
        tags.add(f"rotation-metadata-{info['rotation_deg']}")
    if info["nominal_fps"] == 60:
        tags.add("60fps")

    video: dict[str, Any] = {"nominal_fps": info["nominal_fps"]}
    if info["measured_fps"] is not None:
        video["measured_fps"] = info["measured_fps"]
    video.update(
        {
            "width_px": info["encoded_width_px"],
            "height_px": info["encoded_height_px"],
            "duration_s": info["duration_s"],
            "rotation_deg": info["rotation_deg"],
        }
    )

    source: dict[str, Any] = {"kind": source_kind, "reference": f"Local recording: {path.name}"}
    if rights_holder:
        source["rights_holder"] = rights_holder
    source["redistribution_status"] = "private_only"
    source["redistribution_evidence"] = "Contains identifiable people; private development and validation only."

    fixture: dict[str, Any] = {
        "id": fixture_id,
        "exercise": exercise,
        "purpose": purpose,
        "media": {
            "repository_path": info["repository_path"],
            "sha256": info["sha256"],
        },
        "video": video,
        "camera": {
            "view": camera_view,
            "movement": camera_movement,
        },
        "load": {
            "plate_diameter_m": plate_diameter_m,
        },
        "conditions": {
            "lighting": lighting,
            "plate_visibility": plate_visibility,
            "occlusion": occlusion,
            "motion_blur": motion_blur,
            "challenge_tags": sorted(tags),
        },
        "source": source,
    }
    if notes:
        fixture["notes"] = notes

    validate_manifest({"schema_version": 1, "fixtures": [fixture]})
    return fixture


def validate_manifest(manifest: dict[str, Any]) -> None:
    """Raise ProbeError listing every fixture-manifest-v1 schema violation."""
    errors = schema_check.validate_document(manifest, schema_check.load_schema(MANIFEST_SCHEMA_PATH))
    if errors:
        raise ProbeError("manifest does not match fixture-manifest-v1:\n  " + "\n  ".join(errors))


def is_public_manifest(manifest_path: Path) -> bool:
    return manifest_path.resolve().is_relative_to(PUBLIC_FIXTURES_DIR.resolve())


def append_to_manifest(manifest_path: Path, fixture: dict[str, Any], *, replace: bool = False) -> None:
    """Add a fixture entry, or replace one with the same id when `replace` is set.

    The whole manifest is schema-validated before it is written, and the write is atomic.
    """
    if is_public_manifest(manifest_path) and fixture["source"]["redistribution_status"] != "allowed":
        raise ProbeError(
            f"refusing to add '{fixture['id']}' to public manifest {manifest_path}: "
            f"redistribution_status is {fixture['source']['redistribution_status']!r}, not 'allowed'"
        )

    if manifest_path.is_file():
        try:
            manifest = schema_check.load_strict(manifest_path)
        except schema_check.DocumentError as error:
            raise ProbeError(str(error)) from error
    else:
        manifest = {"schema_version": 1, "fixtures": []}

    fixtures: list[dict[str, Any]] = manifest.get("fixtures", [])
    exists = any(existing.get("id") == fixture["id"] for existing in fixtures)
    if exists and not replace:
        raise ProbeError(f"fixture '{fixture['id']}' already exists in {manifest_path}; pass --replace to overwrite it")
    updated_fixtures = (
        [fixture if existing.get("id") == fixture["id"] else existing for existing in fixtures]
        if exists
        else [*fixtures, fixture]
    )
    updated = {**manifest, "fixtures": updated_fixtures}
    validate_manifest(updated)

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=manifest_path.parent, prefix=f".{manifest_path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as temp:
            temp.write(json.dumps(updated, indent=2) + "\n")
        os.replace(temp_name, manifest_path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    # inspect
    inspect_parser = subparsers.add_parser("inspect", help="Inspect video properties with ffprobe")
    inspect_parser.add_argument("videos", nargs="+", type=Path, help="Video file path(s)")
    inspect_parser.add_argument("--json", action="store_true", help="Output a JSON array")

    # contact-sheet
    cs_parser = subparsers.add_parser("contact-sheet", help="Generate contact sheet preview image")
    cs_parser.add_argument("video", type=Path, help="Video file path")
    cs_parser.add_argument("--output", type=Path, required=True, help="Output image file path")
    cs_parser.add_argument("--rows", type=int, default=3, help="Grid rows (default: 3)")
    cs_parser.add_argument("--cols", type=int, default=4, help="Grid columns (default: 4)")
    cs_parser.add_argument("--thumb-width", type=int, default=320, help="Tile width in pixels")

    # draft
    draft_parser = subparsers.add_parser(
        "draft", help="Draft a fixture manifest entry (defaults: self-recorded side view, good conditions)"
    )
    draft_parser.add_argument("video", type=Path, help="Video file path inside the repository")
    draft_parser.add_argument("--id", required=True, help="Stable fixture ID")
    draft_parser.add_argument("--exercise", required=True, choices=["snatch", "clean", "back_squat", "other"])
    draft_parser.add_argument("--purpose", default="validation", choices=["development", "validation", "control", "boundary", "ood"])
    draft_parser.add_argument("--plate-diameter-m", type=float, default=0.45, help="Plate diameter in metres (default: 0.45)")
    draft_parser.add_argument("--view", default="side", choices=["side", "oblique_45", "front", "rear", "unknown"])
    draft_parser.add_argument("--movement", default="fixed", choices=["fixed", "handheld", "panning", "moving_other", "unknown"])
    draft_parser.add_argument("--lighting", default="good", choices=["good", "mixed", "low", "backlit", "unknown"])
    draft_parser.add_argument("--plate-visibility", default="clear", choices=["clear", "partial", "intermittent", "poor", "unknown"])
    draft_parser.add_argument("--occlusion", default="none", choices=["none", "minor", "moderate", "severe", "unknown"])
    draft_parser.add_argument("--motion-blur", default="moderate", choices=["none", "low", "moderate", "severe", "unknown"])
    draft_parser.add_argument("--tag", action="append", dest="challenge_tags", help="Challenge tags")
    draft_parser.add_argument("--source-kind", default="self_recorded", choices=["self_recorded", "synthetic", "public_dataset", "third_party", "unknown"])
    draft_parser.add_argument("--rights-holder", default="Project owner", help="Rights holder (default: Project owner)")
    draft_parser.add_argument("--notes", default="")
    draft_parser.add_argument("--manifest", type=Path, help="Optional manifest.json path to add the entry to")
    draft_parser.add_argument("--replace", action="store_true", help="Overwrite an existing entry with the same id")

    return parser.parse_args(argv)


def resolve_video_paths(videos: list[Path]) -> list[Path]:
    """Expand globs (for shells that do not) and directories (their *.mp4 files)."""
    resolved: list[Path] = []
    for video in videos:
        if "*" in video.name or "?" in video.name:
            resolved.extend(sorted(video.parent.glob(video.name)))
        elif video.is_dir():
            resolved.extend(sorted(video.glob("*.mp4")))
        else:
            resolved.append(video)
    return resolved


def print_probe(path: Path, info: dict[str, Any]) -> None:
    measured = info["measured_fps"] if info["measured_fps"] is not None else "unknown"
    print(f"=== {path.name} ===")
    print(f"  SHA-256:      {info['sha256']}")
    print(f"  Path:         {info['repository_path'] or '(outside repository)'}")
    print(f"  Duration:     {info['duration_s']:.2f} s ({info['frame_count']} frames)")
    print(f"  FPS:          nominal {info['nominal_fps']}, measured {measured}")
    print(f"  Resolution:   encoded {info['encoded_width_px']}x{info['encoded_height_px']}, rotation {info['rotation_deg']} deg")
    print(f"  Display size: {info['display_width_px']}x{info['display_height_px']}")
    print()


def run_inspect(args: argparse.Namespace) -> int:
    results = []
    failed = False
    for path in resolve_video_paths(args.videos):
        try:
            info = probe_video(path)
        except (ProbeError, OSError) as error:
            print(f"Error inspecting {path}: {error}", file=sys.stderr)
            failed = True
            continue
        results.append(info)
        if not args.json:
            print_probe(path, info)
    if args.json:
        print(json.dumps(results, indent=2))
    return 1 if failed else 0


def run_draft(args: argparse.Namespace) -> int:
    fixture = draft_fixture_manifest(
        args.video,
        fixture_id=args.id,
        exercise=args.exercise,
        purpose=args.purpose,
        plate_diameter_m=args.plate_diameter_m,
        camera_view=args.view,
        camera_movement=args.movement,
        lighting=args.lighting,
        plate_visibility=args.plate_visibility,
        occlusion=args.occlusion,
        motion_blur=args.motion_blur,
        challenge_tags=args.challenge_tags,
        source_kind=args.source_kind,
        rights_holder=args.rights_holder,
        notes=args.notes,
    )
    if args.manifest:
        append_to_manifest(args.manifest, fixture, replace=args.replace)
        print(f"Wrote fixture '{args.id}' to {args.manifest}")
    else:
        print(json.dumps(fixture, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "inspect":
            return run_inspect(args)
        if args.command == "contact-sheet":
            out = generate_contact_sheet(
                args.video,
                args.output,
                rows=args.rows,
                cols=args.cols,
                thumb_width_px=args.thumb_width,
            )
            print(f"Contact sheet saved to {out}")
            return 0
        return run_draft(args)
    except (ProbeError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
