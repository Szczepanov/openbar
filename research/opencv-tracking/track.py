#!/usr/bin/env python3
"""OpenCV tracker spike for #57: research only, not a production tracker.

ADR-0008 allows isolated, clearly non-production spikes that support the rework issues. This one asks
whether mature OpenCV trackers follow the plate better than OpenBar's own M0 candidates, scored by
the same `openbar-cli benchmark` against the same manual annotations.

Dependencies (recorded per docs/architecture/ARCHITECTURE.md "Dependency policy"):
  opencv-contrib-python 4.12.0.88, https://github.com/opencv/opencv-python
    - package/build tooling: MIT; bundled OpenCV 4.12: Apache-2.0; wheel third-party notices apply;
    - purpose: CSRT/KCF trackers (contrib `tracking` module).
  numpy 2.2.6, BSD-3-Clause, https://numpy.org/
    - purpose: decoded-frame array view passed to OpenCV.
  Research venv only; the wheel/venv is not redistributed and nothing in the Rust workspace or
  validation/tools depends on it. Production adoption requires a separate dependency review.

Measurement rules kept from AGENTS.md:
  - frames come from OpenBar's decode contract (ADR-0006: FFmpeg process, -autorotate, passthrough
    timing, first video stream) and timestamps from ffprobe PTS via validation/tools/label_package.py,
    never from cv2.VideoCapture;
  - a frame the tracker loses is written as `lost` with no coordinate;
  - confidence is an algorithm-specific appearance score (normalised cross-correlation of the current
    box against the seed template), clamped to [0, 1]; it is not a calibrated probability;
  - held-out `validation` fixtures are refused unless --allow-held-out is given for the frozen run.

Coordinates follow ADR-0007: integer pixel indices are pixel centres. An OpenCV box (x, y, w, h)
covers pixel columns x .. x+w-1, so its centre is x + (w - 1) / 2.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterator

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "tools"))
import label_package  # noqa: E402  (shared probe/decode contract with the labelling tool)

SPIKE_VERSION = "spike-1"
SEED_TOLERANCE_S = 0.0005
TRACKERS = {
    "csrt": cv2.TrackerCSRT.create,
    "kcf": cv2.TrackerKCF.create,
}
DECODE_ARGS = ["-map", "0:v:0", "-fps_mode", "passthrough", "-enc_time_base", "demux",
               "-pix_fmt", "bgr24", "-f", "rawvideo", "-"]


class SpikeError(RuntimeError):
    pass


def finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def seed_box(center_x: float, center_y: float, radius: float, width: int, height: int) -> tuple[int, int, int, int]:
    """Integer box spanning the seed circle, clipped to the image."""
    x0, y0 = max(0, round(center_x - radius)), max(0, round(center_y - radius))
    x1, y1 = min(width - 1, round(center_x + radius)), min(height - 1, round(center_y + radius))
    if x1 <= x0 or y1 <= y0:
        raise SpikeError("seed box is empty after clipping to the image")
    return x0, y0, x1 - x0 + 1, y1 - y0 + 1


def box_center(box: tuple[float, float, float, float]) -> tuple[float, float]:
    x, y, w, h = box
    return x + (w - 1) / 2, y + (h - 1) / 2


def inside(x: float, y: float, width: int, height: int) -> bool:
    # ADR-0007 asks first-party producers to stay in the intersection of the exact pixel-centre
    # domain and the wider v1 persisted point window.
    return 0 <= x < width - 0.5 and 0 <= y < height - 0.5


def valid_box(box: Any, width: int, height: int) -> bool:
    try:
        x, y, w, h = (float(value) for value in box)
    except (TypeError, ValueError):
        return False
    if not all(math.isfinite(value) for value in (x, y, w, h)) or w <= 0 or h <= 0:
        return False
    cx, cy = box_center((x, y, w, h))
    return inside(cx, cy, width, height)


def appearance_confidence(gray: np.ndarray, box: tuple[float, float, float, float], template: np.ndarray) -> float:
    """Normalised cross-correlation of the tracked box against the seed template, clamped to [0, 1]."""
    x, y, w, h = (int(round(v)) for v in box)
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(gray.shape[1], x + w), min(gray.shape[0], y + h)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return 0.0
    patch = gray[y0:y1, x0:x1]
    resized = cv2.resize(template, (patch.shape[1], patch.shape[0]), interpolation=cv2.INTER_AREA)
    score = float(cv2.matchTemplate(patch, resized, cv2.TM_CCOEFF_NORMED)[0, 0])
    return min(1.0, max(0.0, score)) if math.isfinite(score) else 0.0


def decode_frames(media: Path, width: int, height: int, count: int) -> Iterator[np.ndarray]:
    """BGR frames in decode order; the decoded count must equal the probed count, as in tracker-run."""
    frame_bytes = width * height * 3
    command = ["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-autorotate", "-i", str(media), *DECODE_ARGS]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert process.stdout is not None
    try:
        for index in range(count):
            data = process.stdout.read(frame_bytes)
            if len(data) != frame_bytes:
                raise SpikeError(f"ffmpeg produced {index} frames, ffprobe reported {count}")
            yield np.frombuffer(data, dtype=np.uint8).reshape(height, width, 3)
        if process.stdout.read(1):
            raise SpikeError(f"ffmpeg produced more than the {count} probed frames")
    finally:
        process.stdout.close()
        returncode = process.wait()
    if returncode != 0:
        raise SpikeError(f"ffmpeg exited with status {returncode}")


def load_inputs(manifest_path: Path, fixture_id: str, seed_path: Path, allow_held_out: bool) -> tuple[dict, Path, dict]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    fixture = next((f for f in manifest["fixtures"] if f["id"] == fixture_id), None)
    if fixture is None:
        raise SpikeError(f"fixture {fixture_id!r} is not in {manifest_path}")
    if fixture["purpose"] == "validation" and not allow_held_out:
        raise SpikeError(f"{fixture_id} is a held-out validation fixture; tune on development fixtures only")
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    if seed.get("fixture_id") not in (None, fixture_id):
        raise SpikeError("seed fixture_id does not match")
    media = ROOT / fixture["media"]["repository_path"]
    label_package.require_media_hash(fixture, media)
    return fixture, media, seed


def track(manifest_path: Path, fixture_id: str, seed_path: Path, tracker_name: str,
          end_s: float | None, allow_held_out: bool) -> dict[str, Any]:
    started = time.perf_counter()
    fixture, media, seed_doc = load_inputs(manifest_path, fixture_id, seed_path, allow_held_out)
    probed = label_package.probe(media)
    label_package.require_fixture_probe_match(fixture, probed)
    width, height = label_package.display_size(probed["width_px"], probed["height_px"], probed["rotation_deg"])
    timestamps = probed["timestamps_s"]

    seed = seed_doc["seed"]
    seed_timestamp_s = seed["timestamp_s"]
    center, radius = seed["target"]["center"], seed["target"]["radius_px"]
    if not finite_number(seed_timestamp_s) or float(seed_timestamp_s) < 0:
        raise SpikeError("seed timestamp_s must be a finite non-negative number")
    if not finite_number(center["x_px"]) or not finite_number(center["y_px"]):
        raise SpikeError("seed centre coordinates must be finite numbers")
    if not finite_number(radius) or float(radius) <= 0:
        raise SpikeError("seed radius_px must be a finite positive number")
    if end_s is not None and (not math.isfinite(end_s) or end_s < float(seed_timestamp_s) - SEED_TOLERANCE_S):
        raise SpikeError("--end-s must be finite and must not precede the seed timestamp")

    seed_index = min(range(len(timestamps)), key=lambda i: abs(timestamps[i] - float(seed_timestamp_s)))
    if abs(timestamps[seed_index] - float(seed_timestamp_s)) > SEED_TOLERANCE_S:
        raise SpikeError(f"seed timestamp {seed_timestamp_s} s matches no decoded frame")
    last_s = timestamps[-1] if end_s is None else end_s
    box = seed_box(float(center["x_px"]), float(center["y_px"]), float(radius), width, height)

    cv2.setNumThreads(1)  # single-threaded for reproducible results
    tracker = TRACKERS[tracker_name]()
    template = None
    samples: list[dict[str, Any]] = []
    processing_wall_s: float | None = None
    for index, frame in enumerate(decode_frames(media, width, height, len(timestamps))):
        t = timestamps[index]
        if index < seed_index:
            continue
        if t > last_s + SEED_TOLERANCE_S:
            # Drain the decoder so the ADR-0006 frame-count/exit-status contract is still checked.
            # Runtime below stops at the last processed tracking frame, so this validation drain is
            # not charged to the selected tracking range.
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if index == seed_index:
            tracker.init(frame, box)
            x, y, w, h = box
            template = gray[y:y + h, x:x + w].copy()
            current = box
            ok = True
        else:
            ok, current = tracker.update(frame)
        if ok and valid_box(current, width, height):
            cx, cy = box_center(current)
            samples.append({"timestamp_s": round(t, 6), "state": "tracked",
                            "center_px": {"x_px": round(cx, 3), "y_px": round(cy, 3)},
                            "confidence": round(appearance_confidence(gray, current, template), 4)})
        else:
            samples.append({"timestamp_s": round(t, 6), "state": "lost"})
        processing_wall_s = time.perf_counter() - started

    if not samples or processing_wall_s is None:
        raise SpikeError("selected range produced no tracker samples")

    return {
        "schema_version": 1,
        "fixture_id": fixture_id,
        "source_video_sha256": fixture["media"]["sha256"],
        "coordinate_space": "decoded_display_pixels",
        "implementation": {
            "name": f"opencv-{tracker_name}",
            "version": SPIKE_VERSION,
            "config": {
                "opencv_version": cv2.__version__,
                "numpy_version": np.__version__,
                "tracker": tracker_name,
                "init_box": "seed circle bounding box",
                "confidence": "ncc_to_seed_template_clamped",
                "threads": 1,
                "seed_timestamp_s": seed_timestamp_s,
                "end_s": last_s,
                "decode_validation": "full_clip_count_and_ffmpeg_exit_status",
            },
        },
        "runtime": {"processing_wall_s": processing_wall_s},
        "samples": samples,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--tracker", choices=sorted(TRACKERS), required=True)
    parser.add_argument("--end-s", type=float, help="last timestamp to track (default: end of clip)")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-held-out", action="store_true", help="only for the frozen held-out evaluation")
    args = parser.parse_args(argv)
    try:
        prediction = track(args.manifest, args.fixture, args.seed, args.tracker, args.end_s, args.allow_held_out)
    except (SpikeError, label_package.PackageError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(prediction, indent=2) + "\n", encoding="utf-8")
    tracked = sum(s["state"] == "tracked" for s in prediction["samples"])
    print(f"{prediction['implementation']['name']}: {tracked}/{len(prediction['samples'])} tracked, "
          f"{prediction['runtime']['processing_wall_s']} s -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
