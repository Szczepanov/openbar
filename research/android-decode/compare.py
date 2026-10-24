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
import math
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
    "sample_aspect_ratio,time_base,start_pts:stream_side_data=rotation:frame=pts,color_range"
)
DECODE_MAP_ARGS = (
    "-map", "0:v:0", "-fps_mode", "passthrough", "-enc_time_base", "demux",
)
# ADR-0006 Amendment 1 (frame source version 2): gray is an explicit function of the Y plane.
SUPPORTED_PIX_FMTS = ("yuv420p", "yuvj420p", "yuv422p", "yuvj422p", "yuv444p", "yuvj444p")
IDENTITY_FILTER = "extractplanes=y"
LIMITED_TO_FULL_FILTER = "extractplanes=y,lut=c0='clip(round((val-16)*255/219),0,255)'"
STAGES = (
    "extractor_open_s",
    "codec_configure_s",
    "first_frame_s",
    "decode_loop_s",
    "copy_s",
    "repack_s",
    "scan_s",
    "sink_s",
    "total_s",
)


@dataclass(frozen=True)
class ReferenceProbe:
    width_px: int
    height_px: int
    timestamps_s: tuple[float, ...]
    luma_transform: str = "limited_to_full"
    pix_fmt: str = "yuv420p"

    def yuv_frame_bytes(self) -> int:
        """Bytes per raw frame in the source pixel format (Y plus two chroma planes)."""
        w, h = self.width_px, self.height_px
        if "444" in self.pix_fmt:
            chroma = w * h
        elif "422" in self.pix_fmt:
            chroma = ((w + 1) // 2) * h
        else:
            chroma = ((w + 1) // 2) * ((h + 1) // 2)
        return w * h + 2 * chroma

    @property
    def luma_filter(self) -> str:
        return IDENTITY_FILTER if self.luma_transform == "identity" else LIMITED_TO_FULL_FILTER


def select_luma_transform(
    pix_fmt: str | None, frame_ranges: Sequence[str | None], stream_range: str | None = None
) -> str:
    """Same rule as ``openbar-cli`` ``LumaTransform::select``: the frame-level range decides."""
    if pix_fmt not in SUPPORTED_PIX_FMTS:
        raise ValueError(f"pixel format {pix_fmt} has no canonical gray (8-bit planar YUV only)")
    normalized = {None if r in (None, "unknown") else r for r in frame_ranges}
    if len(normalized) > 1:
        raise ValueError(f"frames disagree on color range: {sorted(map(str, normalized))}")
    frame_range = normalized.pop() if normalized else None
    if frame_range == "pc":
        return "identity"
    if frame_range == "tv":
        return "limited_to_full"
    default = "identity" if pix_fmt.startswith("yuvj") else "limited_to_full"
    if stream_range in ("pc", "tv") and (stream_range == "pc") != (default == "identity"):
        raise ValueError(
            f"frames report no color range and stream tag {stream_range} contradicts {pix_fmt}"
        )
    return default


def parse_probe(probe: dict) -> ReferenceProbe:
    """Display dimensions and ``(pts - start_pts) * time_base`` timestamps from ffprobe JSON."""
    streams = probe.get("streams") or []
    if not streams:
        raise ValueError("ffprobe reported no video stream")
    stream = streams[0]
    if stream.get("sample_aspect_ratio") not in (None, "0:1", "1:1"):
        raise ValueError("reference has non-square pixels")
    time_base = Fraction(stream["time_base"])
    if time_base <= 0:
        raise ValueError("reference time_base must be positive")
    start_pts = int(stream["start_pts"])
    rotation = 0
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            angle = float(side["rotation"])
            if not math.isfinite(angle) or abs(angle) > 360 or angle % 90 != 0:
                raise ValueError("reference rotation must be a whole quarter turn")
            rotation = int(angle) % 360
            break
    width, height = int(stream["width"]), int(stream["height"])
    if width <= 0 or height <= 0:
        raise ValueError("reference dimensions must be positive")
    if rotation % 180 == 90:
        width, height = height, width
    frames = probe.get("frames") or []
    if any("pts" not in frame for frame in frames):
        # Dropping such a frame would shift every later index against the raw FFmpeg stream.
        raise ValueError("ffprobe reported a frame without pts")
    pts = [int(frame["pts"]) for frame in frames]
    timestamps = tuple(float((value - start_pts) * time_base) for value in pts)
    validate_timestamps(timestamps)
    transform = select_luma_transform(
        stream.get("pix_fmt"), [frame.get("color_range") for frame in frames],
        stream.get("color_range"),
    )
    return ReferenceProbe(width, height, timestamps, transform, stream["pix_fmt"])


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


def validate_timestamps(timestamps: Sequence[float]) -> None:
    if not timestamps or any(not math.isfinite(t) or t < 0 for t in timestamps):
        raise ValueError("timestamps must be nonempty, finite and non-negative")
    if any(b <= a for a, b in zip(timestamps, timestamps[1:])):
        raise ValueError("timestamps must be strictly increasing")


def compare_timestamps(
    prototype: Sequence[float], reference: Sequence[float], tolerance_s: float = PTS_TOLERANCE_S
) -> dict:
    validate_timestamps(prototype)
    validate_timestamps(reference)
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


def limited_to_full_table() -> bytes:
    """ADR-0006 expansion ``round((Y-16)*255/219)`` clipped, same integer rule as the prototype."""
    return bytes(min(255, max(0, ((code - 16) * 255 + 109) // 219)) if code > 16 else 0
                 for code in range(256))


def compare_frames(
    prototype: BinaryIO,
    reference: BinaryIO,
    frame_bytes: int,
    reference_indices: Sequence[int],
    reference_frame_bytes: int | None = None,
    transform: bytes | None = None,
) -> dict:
    """Stream both raw files; the reference holds every source frame, the prototype only the window.

    ``reference_frame_bytes`` lets the reference carry extra planes (e.g. yuv420p chroma) after the
    first ``frame_bytes``; ``transform`` is a 256-byte table applied to the reference luma.
    """
    stride = reference_frame_bytes or frame_bytes
    wanted = iter(reference_indices)
    target = next(wanted, None)
    per_frame: list[tuple[int, float]] = []
    index = 0
    while target is not None:
        ref = reference.read(stride)
        if len(ref) < stride:
            raise ValueError(f"reference ended before source frame {target}")
        ref = ref[:frame_bytes]
        if transform is not None:
            ref = ref.translate(transform)
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


def run_ffmpeg_gray(
    ffmpeg: str, video: Path, destination: Path, pix_fmt: str = "gray", vf: str | None = None
) -> None:
    filters = ["-vf", vf] if vf else []
    subprocess.run(
        [ffmpeg, "-nostdin", "-hide_banner", "-v", "error", "-autorotate", "-i", str(video),
         *DECODE_MAP_ARGS, *filters, "-pix_fmt", pix_fmt, "-f", "rawvideo", "-y",
         str(destination)],
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
    decoder_luma: dict | None = None
    if dims_equal and timestamps["count_equal"]:
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "reference.gray"
            run_ffmpeg_gray(args.ffmpeg, Path(args.input), raw, vf=reference.luma_filter)
            frame_bytes = reference.width_px * reference.height_px
            expected = len(reference.timestamps_s) * frame_bytes
            if raw.stat().st_size != expected:
                raise ValueError(
                    f"ffmpeg wrote {raw.stat().st_size} bytes, expected {expected} "
                    f"({len(reference.timestamps_s)} probed frames)"
                )
            with open(args.dump, "rb") as proto, open(raw, "rb") as ref:
                frames = compare_frames(proto, ref, frame_bytes, indices)
            raw.unlink()
            if not args.skip_raw_y:
                # Diagnostic only, not a gate: FFmpeg's decoded Y plane (no -pix_fmt gray
                # conversion), expanded by the transform the reference contract selects.
                # Separates decoder differences from FFmpeg's gray conversion. Decoded in the
                # source pixel format so a yuvj source is not range-converted first.
                yuv = Path(tmp) / "reference.yuv"
                run_ffmpeg_gray(args.ffmpeg, Path(args.input), yuv, pix_fmt=reference.pix_fmt)
                table = limited_to_full_table() if reference.luma_transform == "limited_to_full" else None
                with open(args.dump, "rb") as proto, open(yuv, "rb") as ref:
                    decoder_luma = compare_frames(
                        proto, ref, frame_bytes, indices,
                        reference_frame_bytes=reference.yuv_frame_bytes(),
                        transform=table,
                    )
    passed = bool(dims_equal and timestamps["within_tolerance"] and frames and frames["within_gate"])
    result = {
        "parity_version": PARITY_VERSION,
        "decoder": report["decoder"]["name"],
        "luma_range": report["decoder"]["luma_range"],
        "luma_range_source": report["decoder"]["luma_range_source"],
        "window": window,
        "dimensions": dims,
        "dimensions_equal": dims_equal,
        "reference_luma_transform": reference.luma_transform,
        "timestamps": timestamps,
        "frames": frames,
        "diagnostic_decoder_luma_vs_ffmpeg_raw_y": decoder_luma,
        "pass": passed,
    }
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0 if passed else 1


# Report fields that define a variant; repeats of one case must agree on all of them.
VARIANT_FIELDS = (
    ("frame_mode",),
    ("decoder", "name"),
    ("decoder", "output_path"),
    ("decoder", "priority_hint"),
    ("decoder", "operating_rate_hint"),
    ("decoder", "hint_readback"),
)


def _field(report: dict, path: tuple[str, ...]):
    value = report
    for key in path:
        value = value[key]
    return value


def _spread(values: list[float]) -> dict:
    return {"median": statistics.median(values), "min": min(values), "max": max(values)}


def per_frame_ms(report: dict) -> dict:
    """Codec wait (loop time not spent building, scanning or sinking frames) per decoded output,
    and frame construction per emitted frame, in milliseconds."""
    timings = report["timings"]
    decoded = report["decoded_output_count"]
    emitted = report["frame_count"]
    if decoded <= 0 or emitted <= 0:
        raise ValueError("report has no decoded or emitted frames")
    wait_s = timings["decode_loop_s"] - timings["repack_s"] - timings["scan_s"] - timings["sink_s"]
    return {
        "decode_wait_per_output": 1000.0 * wait_s / decoded,
        "repack_per_frame": 1000.0 * timings["repack_s"] / emitted,
        "copy_per_frame": 1000.0 * timings["copy_s"] / emitted,
        "scan_per_frame": 1000.0 * timings["scan_s"] / emitted,
    }


def summarize_reports(reports: Iterable[dict]) -> dict:
    reports = list(reports)
    if not reports:
        raise ValueError("no reports to summarize")
    for key in ("frame_count", "decoded_output_count"):
        counts = {r[key] for r in reports}
        if len(counts) != 1:
            raise ValueError(f"repeats disagree on {key}: {sorted(counts)}")
    variant = {}
    for path in VARIANT_FIELDS:
        values = [json.dumps(_field(r, path), sort_keys=True) for r in reports]
        if len(set(values)) != 1:
            raise ValueError(f"repeats disagree on {'.'.join(path)}")
        variant[".".join(path)] = _field(reports[0], path)
    per_frame = [per_frame_ms(r) for r in reports]
    return {
        "repeats": len(reports),
        "frame_count": reports[0]["frame_count"],
        "decoded_output_count": reports[0]["decoded_output_count"],
        "decoder": reports[0]["decoder"]["name"],
        "variant": variant,
        "stages_s": {stage: _spread([r["timings"][stage] for r in reports]) for stage in STAGES},
        "per_frame_ms": {key: _spread([p[key] for p in per_frame]) for key in per_frame[0]},
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
    p.add_argument("--skip-raw-y", action="store_true",
                   help="skip the decoder-exactness diagnostic against FFmpeg's raw Y plane")
    p.set_defaults(func=parity)
    s = sub.add_parser("summarize", help="median/min/max stage timings over repeated reports")
    s.add_argument("reports", nargs="+")
    s.set_defaults(func=summarize)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
