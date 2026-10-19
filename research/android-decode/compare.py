"""Parity and latency harness for the #258 AMediaCodec prototype (stdlib only).

``parity`` compares one prototype run (report JSON + ``--dump-gray`` frames) against the
ADR-0006 FFmpeg reference on the same clip and window: frame count, per-frame PTS, display
dimensions and per-frame luma differences. ``summarize`` aggregates repeated reports into
median/min/max per stage, matching the 5-repeat protocol in
``docs/validation/ANDROID_LATENCY_EVIDENCE.md``.

The FFmpeg arguments mirror ``apps/openbar-cli/src/media/{probe,ffmpeg}.rs``.
"""

from __future__ import annotations

import argparse
import json
import operator
import statistics
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import BinaryIO, Iterable, Sequence

PARITY_VERSION = 1
PTS_TOLERANCE_S = 1e-6
# Hardware H.264/HEVC decoding is bit-exact; this only covers range-expansion rounding.
LUMA_MAX_ABS_DIFF_GATE = 2

PROBE_ENTRIES = (
    "stream=index,codec_name,pix_fmt,color_range,color_transfer,width,height,"
    "sample_aspect_ratio,time_base,start_pts:stream_side_data=rotation:frame=pts"
)
DECODE_OUTPUT_ARGS = (
    "-map", "0:v:0", "-fps_mode", "passthrough", "-enc_time_base", "demux",
    "-pix_fmt", "gray", "-f", "rawvideo",
)
STAGES = (
    "extractor_open_s",
    "codec_configure_s",
    "first_frame_s",
    "decode_loop_s",
    "repack_s",
    "sink_s",
    "total_s",
)


@dataclass(frozen=True)
class ReferenceProbe:
    width_px: int
    height_px: int
    timestamps_s: tuple[float, ...]


def parse_probe(probe: dict) -> ReferenceProbe:
    """Display dimensions and ``(pts - start_pts) * time_base`` timestamps from ffprobe JSON."""
    streams = probe.get("streams") or []
    if not streams:
        raise ValueError("ffprobe reported no video stream")
    stream = streams[0]
    time_base = Fraction(stream["time_base"])
    start_pts = int(stream.get("start_pts", 0))
    rotation = 0
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            rotation = int(side["rotation"]) % 360
    width, height = int(stream["width"]), int(stream["height"])
    if rotation % 180 == 90:
        width, height = height, width
    frames = probe.get("frames") or []
    if any("pts" not in frame for frame in frames):
        # Dropping such a frame would shift every later index against the raw FFmpeg stream.
        raise ValueError("ffprobe reported a frame without pts")
    pts = [int(frame["pts"]) for frame in frames]
    timestamps = tuple(float((value - start_pts) * time_base) for value in pts)
    return ReferenceProbe(width, height, timestamps)


def window_indices(
    timestamps_s: Sequence[float], start_s: float | None, end_s: float | None
) -> list[int]:
    """Indices inside the inclusive window, same rule as ``TimeRange::contains``."""
    lo = 0.0 if start_s is None else start_s
    hi = float("inf") if end_s is None else end_s
    return [i for i, t in enumerate(timestamps_s) if lo <= t <= hi]


def frame_diff(a: bytes, b: bytes) -> tuple[int, float]:
    """Max and mean absolute difference between two equally sized 8-bit frames."""
    if len(a) != len(b):
        raise ValueError(f"frame sizes differ: {len(a)} vs {len(b)}")
    if a == b:
        return 0, 0.0
    diffs = list(map(abs, map(operator.sub, a, b)))
    return max(diffs), sum(diffs) / len(diffs)


def compare_timestamps(
    prototype: Sequence[float], reference: Sequence[float], tolerance_s: float = PTS_TOLERANCE_S
) -> dict:
    worst = max(
        (abs(p - r) for p, r in zip(prototype, reference)),
        default=0.0,
    )
    return {
        "prototype_count": len(prototype),
        "reference_count": len(reference),
        "count_equal": len(prototype) == len(reference),
        "max_abs_diff_s": worst,
        "within_tolerance": len(prototype) == len(reference) and worst <= tolerance_s * (1 + 1e-9),
    }


def compare_frames(
    prototype: BinaryIO,
    reference: BinaryIO,
    frame_bytes: int,
    reference_indices: Sequence[int],
) -> dict:
    """Stream both raw files; the reference holds every source frame, the prototype only the window."""
    wanted = iter(reference_indices)
    target = next(wanted, None)
    per_frame: list[tuple[int, float]] = []
    index = 0
    while target is not None:
        ref = reference.read(frame_bytes)
        if len(ref) < frame_bytes:
            raise ValueError(f"reference ended before source frame {target}")
        if index == target:
            proto = prototype.read(frame_bytes)
            if len(proto) < frame_bytes:
                raise ValueError(f"prototype dump ended at window frame {len(per_frame)}")
            per_frame.append(frame_diff(proto, ref))
            target = next(wanted, None)
        index += 1
    if prototype.read(1):
        raise ValueError("prototype dump has more frames than the reference window")
    max_abs = max((m for m, _ in per_frame), default=0)
    return {
        "frames_compared": len(per_frame),
        "max_abs_diff": max_abs,
        "mean_abs_diff": statistics.fmean(mean for _, mean in per_frame) if per_frame else 0.0,
        "frames_bit_exact": sum(1 for m, _ in per_frame if m == 0),
        "within_gate": max_abs <= LUMA_MAX_ABS_DIFF_GATE,
    }


def run_ffprobe(ffprobe: str, video: Path) -> dict:
    out = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", PROBE_ENTRIES,
         "-of", "json", "-i", str(video)],
        check=True, capture_output=True,
    )
    return json.loads(out.stdout)


def run_ffmpeg_gray(ffmpeg: str, video: Path, destination: Path) -> None:
    subprocess.run(
        [ffmpeg, "-nostdin", "-hide_banner", "-v", "error", "-autorotate", "-i", str(video),
         *DECODE_OUTPUT_ARGS, "-y", str(destination)],
        check=True,
    )


def parity(args: argparse.Namespace) -> int:
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    window = report["window"]
    reference = parse_probe(run_ffprobe(args.ffprobe, Path(args.input)))
    indices = window_indices(reference.timestamps_s, window["start_s"], window["end_s"])
    timestamps = compare_timestamps(
        report["timestamps_s"], [reference.timestamps_s[i] for i in indices]
    )
    dims = {
        "prototype": [report["display_width_px"], report["display_height_px"]],
        "reference": [reference.width_px, reference.height_px],
    }
    dims_equal = dims["prototype"] == dims["reference"]
    frames: dict | None = None
    if dims_equal and timestamps["count_equal"]:
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "reference.gray"
            run_ffmpeg_gray(args.ffmpeg, Path(args.input), raw)
            frame_bytes = reference.width_px * reference.height_px
            expected = len(reference.timestamps_s) * frame_bytes
            if raw.stat().st_size != expected:
                raise ValueError(
                    f"ffmpeg wrote {raw.stat().st_size} bytes, expected {expected} "
                    f"({len(reference.timestamps_s)} probed frames)"
                )
            with open(args.dump, "rb") as proto, open(raw, "rb") as ref:
                frames = compare_frames(proto, ref, frame_bytes, indices)
    passed = bool(dims_equal and timestamps["within_tolerance"] and frames and frames["within_gate"])
    result = {
        "parity_version": PARITY_VERSION,
        "decoder": report["decoder"]["name"],
        "luma_range": report["decoder"]["luma_range"],
        "luma_range_source": report["decoder"]["luma_range_source"],
        "window": window,
        "dimensions": dims,
        "dimensions_equal": dims_equal,
        "timestamps": timestamps,
        "frames": frames,
        "pass": passed,
    }
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0 if passed else 1


def summarize_reports(reports: Iterable[dict]) -> dict:
    reports = list(reports)
    if not reports:
        raise ValueError("no reports to summarize")
    counts = {r["frame_count"] for r in reports}
    if len(counts) != 1:
        raise ValueError(f"repeats decoded different frame counts: {sorted(counts)}")
    stages = {}
    for stage in STAGES:
        values = [r["timings"][stage] for r in reports]
        stages[stage] = {
            "median": statistics.median(values),
            "min": min(values),
            "max": max(values),
        }
    return {
        "repeats": len(reports),
        "frame_count": counts.pop(),
        "decoder": reports[0]["decoder"]["name"],
        "stages_s": stages,
    }


def summarize(args: argparse.Namespace) -> int:
    reports = [json.loads(Path(p).read_text(encoding="utf-8")) for p in args.reports]
    sys.stdout.write(json.dumps(summarize_reports(reports), indent=2, sort_keys=True) + "\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("parity", help="compare one prototype run against FFmpeg")
    p.add_argument("--input", required=True, help="the same video the prototype decoded")
    p.add_argument("--report", required=True, help="prototype --report JSON")
    p.add_argument("--dump", required=True, help="prototype --dump-gray raw frames")
    p.add_argument("--output", help="write the parity JSON here as well as stdout")
    p.add_argument("--ffmpeg", default="ffmpeg")
    p.add_argument("--ffprobe", default="ffprobe")
    p.set_defaults(func=parity)
    s = sub.add_parser("summarize", help="median/min/max stage timings over repeated reports")
    s.add_argument("reports", nargs="+")
    s.set_defaults(func=summarize)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
