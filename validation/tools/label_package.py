#!/usr/bin/env python3
"""Build a local plate-centre labelling package for one fixture.

The package is a folder of decoded frames, an annotation metadata sidecar, and a self-contained
``index.html`` labelling page that exports the CSV accepted by ``annotations.py import-csv``.

Frames are decoded the same way as the OpenBar frame source (ADR-0006): FFmpeg runs as an external
process with display rotation applied, passthrough timing and the first video stream. Each frame is
stamped with ``(pts - start_pts) * time_base`` from ``ffprobe``, and every extracted frame's PTS is
checked against the probe so a decoder drop or duplicate cannot silently shift labels onto the wrong
timestamp. Frames are chosen on a uniform time grid, without reference to tracker output, so the
labeller is not anchored on what is being evaluated.

Standard library only. FFmpeg/ffprobe must be on PATH.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import re
import subprocess
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PAGE_TEMPLATE = Path(__file__).with_name("label_page.html")
CONFIG_PLACEHOLDER = "/*CONFIG*/null"
PRIVATE_ROOT = ROOT / "validation" / "private"
TOOL = {"name": "openbar-label-package", "version": "1"}
# One select term per frame keeps the FFmpeg command well under the Windows command-line limit.
MAX_FRAMES = 2000
SHOWINFO = re.compile(r"\[Parsed_showinfo[^\]]*\] n:\s*\d+ pts:\s*(-?\d+)")


class PackageError(RuntimeError):
    pass


def display_size(width_px: int, height_px: int, rotation_deg: int) -> tuple[int, int]:
    """Display-oriented size: 90/270 degree rotation metadata swaps the encoded raster."""
    return (height_px, width_px) if rotation_deg % 180 == 90 else (width_px, height_px)


def parse_probe(text: str) -> dict[str, Any]:
    """Validate ffprobe JSON with the same rules as the Rust frame source (media/probe.rs)."""
    document = json.loads(text)
    streams, frames = document.get("streams") or [], document.get("frames") or []
    if not streams or not frames:
        raise PackageError("ffprobe found no video stream or no frames")
    stream = streams[0]
    if stream.get("sample_aspect_ratio") not in (None, "1:1", "0:1"):
        raise PackageError(f"unsupported sample aspect ratio {stream['sample_aspect_ratio']}")
    rotation = next((side["rotation"] for side in stream.get("side_data_list", []) if "rotation" in side), 0)
    if not float(rotation).is_integer() or abs(rotation) > 360 or rotation % 90 != 0:
        raise PackageError(f"unsupported rotation {rotation}")
    if any("pts" not in frame for frame in frames):
        raise PackageError("a decoded frame has no pts")
    pts = [int(frame["pts"]) for frame in frames]
    start_pts, time_base = int(stream["start_pts"]), Fraction(stream["time_base"])
    if any(value < start_pts for value in pts):
        raise PackageError("a frame pts precedes the stream start")
    if any(later <= earlier for earlier, later in zip(pts, pts[1:])):
        raise PackageError("frame pts are not strictly increasing")
    return {
        "pts": pts,
        "timestamps_s": [float((value - start_pts) * time_base) for value in pts],
        "width_px": int(stream["width"]),
        "height_px": int(stream["height"]),
        "rotation_deg": int(rotation) % 360,
    }


def select_frames(
    timestamps_s: list[float], start_s: float, end_s: float, step_s: float, include: list[int]
) -> list[int]:
    """Indices of the decoded frames nearest to each grid time, plus any explicitly included frame."""
    if step_s <= 0 or end_s < start_s:
        raise PackageError("grid needs step > 0 and end >= start")
    if any(index < 0 or index >= len(timestamps_s) for index in include):
        raise PackageError(f"--include-frame must be within 0..{len(timestamps_s) - 1}")
    chosen = set(include)
    for step in range(int((end_s - start_s) / step_s + 1e-9) + 1):
        target = start_s + step * step_s
        right = bisect.bisect_left(timestamps_s, target)
        candidates = [index for index in (right - 1, right) if 0 <= index < len(timestamps_s)]
        chosen.add(min(candidates, key=lambda index: abs(timestamps_s[index] - target)))
    if len(chosen) > MAX_FRAMES:
        raise PackageError(f"{len(chosen)} frames requested; split the grid into packages of <= {MAX_FRAMES}")
    return sorted(chosen)


def parse_showinfo(stderr: str) -> list[int]:
    return [int(match) for match in SHOWINFO.findall(stderr)]


def require_aligned(extracted_pts: list[int], expected_pts: list[int]) -> None:
    """Every extracted frame must be exactly the probed frame it will be stamped with."""
    if extracted_pts != expected_pts:
        raise PackageError(
            f"extracted frames do not match the probed frames (got {len(extracted_pts)} pts, "
            f"expected {len(expected_pts)}); the decoder dropped or duplicated frames"
        )


def metadata(fixture: dict[str, Any], size: tuple[int, int], annotator_id: str, notes: str) -> dict[str, Any]:
    """Annotation import sidecar; ``annotated_at`` is filled in when the CSV is imported."""
    return {
        "schema_version": 1,
        "fixture_id": fixture["id"],
        "source_video_sha256": fixture["media"]["sha256"],
        "coordinate_system": {
            "space": "decoded_display_pixels",
            "origin": "top_left",
            "x_direction": "right",
            "y_direction": "down",
            "rotation_applied": True,
            "width_px": size[0],
            "height_px": size[1],
        },
        "timebase": {"unit": "seconds", "origin": "media_start", "decoder_match_tolerance_s": 0.0005},
        "provenance": {
            "annotator_id": annotator_id,
            "annotated_at": "FILL-AT-IMPORT",
            "method": "manual_plate_centre",
            "tool": TOOL,
            "notes": notes,
        },
    }


def page_config(fixture_id: str, annotator_id: str, size: tuple[int, int], frames: list[dict[str, Any]]) -> dict[str, Any]:
    """Page configuration; ``package_id`` keeps browser storage separate per pass and grid."""
    identity = json.dumps({"fixture_id": fixture_id, "annotator_id": annotator_id, "frames": frames}, sort_keys=True)
    return {
        "fixture_id": fixture_id,
        "annotator_id": annotator_id,
        "package_id": hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16],
        "width_px": size[0],
        "height_px": size[1],
        "frames": frames,
    }


def render_page(template: str, config: dict[str, Any]) -> str:
    if template.count(CONFIG_PLACEHOLDER) != 1:
        raise PackageError(f"label page template must contain {CONFIG_PLACEHOLDER} exactly once")
    return template.replace(CONFIG_PLACEHOLDER, json.dumps(config, sort_keys=True).replace("</", "<\\/"))


def require_safe_output(fixture: dict[str, Any], output_dir: Path) -> None:
    """Frames of non-redistributable media may only be written below git-ignored validation/private/."""
    if fixture["source"]["redistribution_status"] == "allowed":
        return
    if not output_dir.resolve().is_relative_to(PRIVATE_ROOT.resolve()):
        raise PackageError(
            f"{fixture['id']} is not redistributable; write its frames under "
            f"{PRIVATE_ROOT.relative_to(ROOT).as_posix()}/"
        )


def require_media_hash(fixture: dict[str, Any], media: Path) -> None:
    digest = hashlib.sha256()
    with media.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != fixture["media"]["sha256"]:
        raise PackageError(f"{media} does not match the manifest sha256 for {fixture['id']}")


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            command, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
    except FileNotFoundError as error:
        raise PackageError(f"{command[0]} is not on PATH") from error
    if completed.returncode != 0:
        raise PackageError(f"{command[0]} failed: {completed.stderr.strip()[-500:]}")
    return completed


def probe(media: Path) -> dict[str, Any]:
    return parse_probe(run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,sample_aspect_ratio,time_base,start_pts:stream_side_data=rotation:frame=pts",
         "-of", "json", str(media)]
    ).stdout)


def extract(media: Path, indices: list[int], expected_pts: list[int], frames_dir: Path, fixture: dict[str, Any]) -> list[Path]:
    frames_dir.mkdir(parents=True, exist_ok=True)
    require_safe_output(fixture, frames_dir)  # also catches a symlinked frames/ directory
    for stale in [*frames_dir.glob("frame_*.png"), *frames_dir.glob("tmp_*.png")]:
        stale.unlink()
    select = "+".join(f"eq(n\\,{index})" for index in indices)
    completed = run(["ffmpeg", "-nostdin", "-hide_banner", "-v", "info", "-autorotate", "-i", str(media),
                     "-map", "0:v:0", "-vf", f"select='{select}',showinfo", "-fps_mode", "passthrough",
                     str(frames_dir / "tmp_%06d.png")])
    require_aligned(parse_showinfo(completed.stderr), expected_pts)
    produced = sorted(frames_dir.glob("tmp_*.png"))
    if len(produced) != len(indices):
        raise PackageError(f"ffmpeg wrote {len(produced)} frames, expected {len(indices)}")
    named = []
    for path, index in zip(produced, indices):
        target = frames_dir / f"frame_{index:06d}.png"
        path.replace(target)
        named.append(target)
    return named


def write_text(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def build(args: argparse.Namespace) -> Path:
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    fixture = next((item for item in manifest["fixtures"] if item["id"] == args.fixture), None)
    if fixture is None:
        raise PackageError(f"fixture {args.fixture!r} is not in {args.manifest}")
    private = fixture["source"]["redistribution_status"] != "allowed"
    default_root = PRIVATE_ROOT / "annotations" / "work" if private else ROOT / "target" / "label-packages"
    output_dir = args.output_dir or default_root / f"{fixture['id']}.{args.annotator_id}"
    require_safe_output(fixture, output_dir)

    media = ROOT / fixture["media"]["repository_path"]
    require_media_hash(fixture, media)
    probed = probe(media)
    timestamps = probed["timestamps_s"]
    end_s = timestamps[-1] if args.end_s is None else args.end_s
    indices = select_frames(timestamps, args.start_s, end_s, args.step_s, args.include_frame)
    size = display_size(probed["width_px"], probed["height_px"], probed["rotation_deg"])
    frames = extract(media, indices, [probed["pts"][index] for index in indices], output_dir / "frames", fixture)

    notes = f"Uniform {args.step_s} s grid over {args.start_s}-{end_s:.3f} s; labeller blind to tracker output."
    write_text(output_dir / "metadata.json",
               json.dumps(metadata(fixture, size, args.annotator_id, notes), indent=2) + "\n")
    config = page_config(fixture["id"], args.annotator_id, size, [
        {"file": f"frames/{path.name}", "frame_index": index, "timestamp_s": round(timestamps[index], 6)}
        for path, index in zip(frames, indices)
    ])
    write_text(output_dir / "index.html", render_page(PAGE_TEMPLATE.read_text(encoding="utf-8"), config))
    return output_dir


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    result.add_argument("--manifest", type=Path, required=True)
    result.add_argument("--fixture", required=True)
    result.add_argument("--step-s", type=float, required=True, help="grid spacing in seconds")
    result.add_argument("--start-s", type=float, default=0.0)
    result.add_argument("--end-s", type=float, help="defaults to the last decoded frame")
    result.add_argument("--include-frame", type=int, action="append", default=[],
                        help="also include this decoded frame index (e.g. the manual seed frame)")
    result.add_argument("--annotator-id", default="annotator-a", help="pseudonymous annotator id")
    result.add_argument("--output-dir", type=Path)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        output_dir = build(args)
    except (PackageError, OSError, KeyError, ValueError) as error:
        print(f"label-package: {error}", file=sys.stderr)
        return 1
    print(f"open {output_dir / 'index.html'} in a browser; import the downloaded CSV with metadata.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
