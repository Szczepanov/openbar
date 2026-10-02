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
import hashlib
import json
import math
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterator

import cv2
import numpy as np

import point_motion
import refine_circle

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "tools"))
import label_package  # noqa: E402  (shared probe/decode contract with the labelling tool)

SPIKE_VERSION = "spike-1"
SEED_TOLERANCE_S = 0.0005
MODELS_DIR = Path(__file__).resolve().parent / "models"
FALLBACK_MODELS_DIR = ROOT / "validation" / "private" / "models"

MODELS: dict[str, dict[str, Any]] = {
    "vit": {
        "license": "Apache-2.0",
        "description": "ViTTrack (Vision Transformer Tracker)",
        "files": [
            {
                "role": "net",
                "filename": "object_tracking_vittrack_2023sep.onnx",
                "sha256": "2990f0b7cd44d92afa48cd97db6de7be113fc1d9594fddb74e2725c10478e91d",
                "source": "https://huggingface.co/opencv/object_tracking_vittrack/resolve/main/object_tracking_vittrack_2023sep.onnx",
                "upstream": "opencv/opencv_zoo",
            }
        ],
    },
    "nano": {
        "license": "Unconfirmed / all rights reserved (not shippable without confirmation)",
        "description": "NanoTrack v2",
        "files": [
            {
                "role": "backbone",
                "filename": "nanotrack_backbone_sim.onnx",
                "sha256": "530bdd0cd00f19afab79a863e71ba71e3312395a5dc9151af675082bdaaa2fc4",
                "source": "https://github.com/HonglinChu/SiamTrackers/raw/master/NanoTrack/models/nanotrackv2/nanotrack_backbone_sim.onnx",
                "upstream": "HonglinChu/SiamTrackers",
            },
            {
                "role": "neckhead",
                "filename": "nanotrack_head_sim.onnx",
                "sha256": "0d8c0637be849f092cc7236cae02e55c8b9455ebe37ba50601d6115db4247cd9",
                "source": "https://github.com/HonglinChu/SiamTrackers/raw/master/NanoTrack/models/nanotrackv2/nanotrack_head_sim.onnx",
                "upstream": "HonglinChu/SiamTrackers",
            },
        ],
    },
    "dasiamrpn": {
        "license": "MIT",
        "description": "DaSiamRPN",
        "files": [
            {
                "role": "model",
                "filename": "dasiamrpn_model.onnx",
                "sha256": "e88370b85cbad914a5eb414d9d9e0820f87fd0cd89b65205a766174206c35719",
                "source": "https://files.kde.org/kdenlive/motion-tracker/DaSiamRPN/dasiamrpn_model.onnx",
                "upstream": "foolwood/DaSiamRPN",
            },
            {
                "role": "kernel_r1",
                "filename": "dasiamrpn_kernel_r1.onnx",
                "sha256": "082c85d231b88b97a1b2a50e73b640a332c5d98d7c1d80b5da9ab534fa7a9e5b",
                "source": "https://files.kde.org/kdenlive/motion-tracker/DaSiamRPN/dasiamrpn_kernel_r1.onnx",
                "upstream": "foolwood/DaSiamRPN",
            },
            {
                "role": "kernel_cls1",
                "filename": "dasiamrpn_kernel_cls1.onnx",
                "sha256": "d85b03e2aeded6cc9be945dfdc3ed6b8f4151f101e485037b6c5d5b36a6c4204",
                "source": "https://files.kde.org/kdenlive/motion-tracker/DaSiamRPN/dasiamrpn_kernel_cls1.onnx",
                "upstream": "foolwood/DaSiamRPN",
            },
        ],
    },
}


def resolve_model_file(filename: str, expected_sha256: str) -> Path:
    candidates = [MODELS_DIR / filename, FALLBACK_MODELS_DIR / filename]
    for path in candidates:
        if path.is_file():
            h = hashlib.sha256()
            with open(path, "rb") as f:
                while chunk := f.read(65536):
                    h.update(chunk)
            digest = h.hexdigest().lower()
            if digest != expected_sha256.lower():
                raise SpikeError(f"model file {path} SHA-256 mismatch: expected {expected_sha256}, got {digest}")
            return path
    raise SpikeError(
        f"model file {filename!r} not found in {MODELS_DIR} or {FALLBACK_MODELS_DIR}. Run download_models.py first."
    )


def create_csrt() -> cv2.Tracker:
    return cv2.TrackerCSRT.create()


def create_kcf() -> cv2.Tracker:
    return cv2.TrackerKCF.create()


def create_vit() -> cv2.Tracker:
    spec = MODELS["vit"]
    f = spec["files"][0]
    path = resolve_model_file(f["filename"], f["sha256"])
    params = cv2.TrackerVit_Params()
    params.net = str(path)
    return cv2.TrackerVit.create(params)


def create_nano() -> cv2.Tracker:
    spec = MODELS["nano"]
    backbone_f = next(f for f in spec["files"] if f["role"] == "backbone")
    head_f = next(f for f in spec["files"] if f["role"] == "neckhead")
    bb_path = resolve_model_file(backbone_f["filename"], backbone_f["sha256"])
    head_path = resolve_model_file(head_f["filename"], head_f["sha256"])
    params = cv2.TrackerNano_Params()
    params.backbone = str(bb_path)
    params.neckhead = str(head_path)
    return cv2.TrackerNano.create(params)


def create_dasiamrpn() -> cv2.Tracker:
    spec = MODELS["dasiamrpn"]
    m_f = next(f for f in spec["files"] if f["role"] == "model")
    r1_f = next(f for f in spec["files"] if f["role"] == "kernel_r1")
    cls1_f = next(f for f in spec["files"] if f["role"] == "kernel_cls1")
    m_path = resolve_model_file(m_f["filename"], m_f["sha256"])
    r1_path = resolve_model_file(r1_f["filename"], r1_f["sha256"])
    cls1_path = resolve_model_file(cls1_f["filename"], cls1_f["sha256"])
    params = cv2.TrackerDaSiamRPN_Params()
    params.model = str(m_path)
    params.kernel_r1 = str(r1_path)
    params.kernel_cls1 = str(cls1_path)
    return cv2.TrackerDaSiamRPN.create(params)


TRACKERS = {
    "csrt": create_csrt,
    "kcf": create_kcf,
    "vit": create_vit,
    "nano": create_nano,
    "dasiamrpn": create_dasiamrpn,
}

REFINEMENT_VARIANTS: dict[str, dict[str, Any]] = {
    "csrt+circle-a": {"base": "csrt", "method": "circle", "reinit_period": None},
    "csrt+circle-b1": {"base": "csrt", "method": "circle", "reinit_period": 1},
    "csrt+circle-b5": {"base": "csrt", "method": "circle", "reinit_period": 5},
    "csrt+hough": {"base": "csrt", "method": "hough", "reinit_period": None},
}

POINT_MOTION_VARIANTS: dict[str, dict[str, Any]] = {
    "lk-affine": {"fallback": None},
    "csrt+lk": {"fallback": "csrt"},
}

ALL_TRACKERS = sorted(list(TRACKERS.keys()) + list(REFINEMENT_VARIANTS.keys()) + list(POINT_MOTION_VARIANTS.keys()))
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
          end_s: float | None, allow_held_out: bool) -> tuple[dict[str, Any], dict[str, Any] | None]:
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

    is_refined = tracker_name in REFINEMENT_VARIANTS
    is_point_motion = tracker_name in POINT_MOTION_VARIANTS
    ref_spec = REFINEMENT_VARIANTS.get(tracker_name)
    base_tracker_name = ref_spec["base"] if is_refined else tracker_name

    cv2.setNumThreads(1)  # single-threaded for reproducible results
    tracker = None
    if not is_point_motion or tracker_name == "csrt+lk":
        tracker_to_create = "csrt" if is_point_motion else base_tracker_name
        tracker = TRACKERS[tracker_to_create]()
    template = None
    samples: list[dict[str, Any]] = []
    sidecar_samples: list[dict[str, Any]] = []
    processing_wall_s: float | None = None

    r_prev = float(radius)
    r_seed = float(radius)

    seed_pts: np.ndarray | None = None
    cur_pts: np.ndarray | None = None
    surviving_indices: np.ndarray | None = None
    prev_gray: np.ndarray | None = None
    initial_query_count = 0

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
            cx, cy = float(center["x_px"]), float(center["y_px"])
            if tracker is not None:
                tracker.init(frame, box)
                x, y, w, h = box
                template = gray[y:y + h, x:x + w].copy()
            if is_point_motion:
                query_pts = point_motion.extract_query_points(gray, cx, cy, float(radius))
                initial_query_count = len(query_pts)
                seed_pts = query_pts.copy()
                cur_pts = query_pts.copy()
                surviving_indices = np.arange(initial_query_count)
                prev_gray = gray.copy()

            samples.append({"timestamp_s": round(t, 6), "state": "tracked",
                            "center_px": {"x_px": round(cx, 3), "y_px": round(cy, 3)},
                            "confidence": 1.0})
            if is_refined:
                sidecar_samples.append({
                    "timestamp_s": round(t, 6),
                    "fit_attempted": False,
                    "accepted": False,
                    "reject_reasons": [],
                    "radius_px": round(r_seed, 3),
                    "inlier_count": None,
                    "edge_count": None,
                    "coverage_bins": None,
                    "canny_lower": None,
                    "canny_upper": None,
                    "ellipse": None,
                    "base_confidence": 1.0,
                })
            elif tracker_name == "csrt+lk":
                sidecar_samples.append({
                    "timestamp_s": round(t, 6),
                    "fit_attempted": False,
                    "accepted": True,
                    "reject_reasons": [],
                    "base_confidence": 1.0,
                })
        else:
            frame_offset = index - seed_index
            if is_point_motion:
                assert prev_gray is not None and cur_pts is not None and seed_pts is not None and surviving_indices is not None
                next_pts, valid = point_motion.track_points_lk(prev_gray, gray, cur_pts)
                surviving_indices = surviving_indices[valid]
                cur_pts = next_pts[valid]
                prev_gray = gray.copy()
                res = point_motion.estimate_center_from_points(
                    seed_pts[surviving_indices],
                    cur_pts,
                    (float(center["x_px"]), float(center["y_px"])),
                    initial_query_count,
                    width,
                    height,
                )
                if tracker_name == "lk-affine":
                    if res.state == "tracked":
                        assert res.center_px is not None
                        samples.append({
                            "timestamp_s": round(t, 6),
                            "state": "tracked",
                            "center_px": {"x_px": round(res.center_px[0], 3), "y_px": round(res.center_px[1], 3)},
                            "confidence": round(res.confidence, 4),
                        })
                    else:
                        samples.append({"timestamp_s": round(t, 6), "state": "lost"})
                elif tracker_name == "csrt+lk":
                    assert tracker is not None
                    csrt_ok, csrt_current = tracker.update(frame)
                    csrt_conf = 0.0
                    if csrt_ok:
                        if hasattr(tracker, "getTrackingScore"):
                            raw_score = float(tracker.getTrackingScore())
                            csrt_conf = min(1.0, max(0.0, raw_score)) if math.isfinite(raw_score) else 0.0
                        else:
                            assert template is not None
                            csrt_conf = appearance_confidence(gray, csrt_current, template)
                    if res.state == "tracked":
                        assert res.center_px is not None
                        samples.append({
                            "timestamp_s": round(t, 6),
                            "state": "tracked",
                            "center_px": {"x_px": round(res.center_px[0], 3), "y_px": round(res.center_px[1], 3)},
                            "confidence": round(res.confidence, 4),
                        })
                        sidecar_samples.append({
                            "timestamp_s": round(t, 6),
                            "fit_attempted": True,
                            "accepted": True,
                            "reject_reasons": [],
                            "base_confidence": round(res.confidence, 4),
                        })
                    elif csrt_ok and valid_box(csrt_current, width, height):
                        cx_b, cy_b = box_center(csrt_current)
                        samples.append({
                            "timestamp_s": round(t, 6),
                            "state": "tracked",
                            "center_px": {"x_px": round(cx_b, 3), "y_px": round(cy_b, 3)},
                            "confidence": round(csrt_conf * 0.7, 4),
                        })
                        sidecar_samples.append({
                            "timestamp_s": round(t, 6),
                            "fit_attempted": True,
                            "accepted": False,
                            "reject_reasons": ["lk_lost"],
                            "base_confidence": round(csrt_conf, 4),
                        })
                    else:
                        samples.append({"timestamp_s": round(t, 6), "state": "lost"})
                        sidecar_samples.append({
                            "timestamp_s": round(t, 6),
                            "fit_attempted": True,
                            "accepted": False,
                            "reject_reasons": ["lk_lost", "csrt_lost"],
                            "base_confidence": 0.0,
                        })
            else:
                assert tracker is not None
                ok, current = tracker.update(frame)
                if ok:
                    if hasattr(tracker, "getTrackingScore"):
                        raw_score = float(tracker.getTrackingScore())
                        base_conf = min(1.0, max(0.0, raw_score)) if math.isfinite(raw_score) else 0.0
                    else:
                        assert template is not None
                        base_conf = appearance_confidence(gray, current, template)
                else:
                    base_conf = 0.0

                if not is_refined:
                    if ok and valid_box(current, width, height):
                        cx, cy = box_center(current)
                        samples.append({"timestamp_s": round(t, 6), "state": "tracked",
                                        "center_px": {"x_px": round(cx, 3), "y_px": round(cy, 3)},
                                        "confidence": round(base_conf, 4)})
                    else:
                        samples.append({"timestamp_s": round(t, 6), "state": "lost"})
                else:
                    if ok and valid_box(current, width, height):
                        assert ref_spec is not None
                        ref_res = refine_circle.refine_circle(
                            frame,
                            current,
                            r_prev,
                            r_seed,
                            base_conf,
                            frame_offset,
                            seed_template=template,
                            method=ref_spec["method"],
                        )
                        if ref_res.accepted:
                            r_prev = ref_res.radius_px
                            reinit_period = ref_spec["reinit_period"]
                            if reinit_period is not None and (frame_offset % reinit_period == 0):
                                reinit_box = seed_box(
                                    ref_res.center_px[0],
                                    ref_res.center_px[1],
                                    ref_res.radius_px,
                                    width,
                                    height,
                                )
                                tracker = TRACKERS[base_tracker_name]()
                                tracker.init(frame, reinit_box)

                        samples.append({
                            "timestamp_s": round(t, 6),
                            "state": "tracked",
                            "center_px": {
                                "x_px": round(ref_res.center_px[0], 3),
                                "y_px": round(ref_res.center_px[1], 3),
                            },
                            "confidence": round(ref_res.confidence, 4),
                        })
                        sidecar_samples.append({
                            "timestamp_s": round(t, 6),
                            "fit_attempted": ref_res.fit_attempted,
                            "accepted": ref_res.accepted,
                            "reject_reasons": ref_res.reject_reasons,
                            "radius_px": round(ref_res.radius_px, 3) if ref_res.radius_px is not None else None,
                            "inlier_count": ref_res.inlier_count,
                            "edge_count": ref_res.edge_count,
                            "coverage_bins": ref_res.coverage_bins,
                            "canny_lower": ref_res.canny_lower,
                            "canny_upper": ref_res.canny_upper,
                            "ellipse": ref_res.ellipse,
                            "base_confidence": round(base_conf, 4),
                        })
                    else:
                        samples.append({"timestamp_s": round(t, 6), "state": "lost"})
                        sidecar_samples.append({
                            "timestamp_s": round(t, 6),
                            "fit_attempted": False,
                            "accepted": False,
                            "reject_reasons": ["base_lost"],
                            "radius_px": None,
                            "inlier_count": None,
                            "edge_count": None,
                            "coverage_bins": None,
                            "canny_lower": None,
                            "canny_upper": None,
                            "ellipse": None,
                            "base_confidence": 0.0,
                        })

        processing_wall_s = time.perf_counter() - started

    if not samples or processing_wall_s is None:
        raise SpikeError("selected range produced no tracker samples")

    config: dict[str, Any] = {
        "opencv_version": cv2.__version__,
        "numpy_version": np.__version__,
        "tracker": tracker_name,
        "base_tracker": base_tracker_name,
        "init_box": "seed circle bounding box",
        "threads": 1,
        "seed_timestamp_s": seed_timestamp_s,
        "end_s": last_s,
        "decode_validation": "full_clip_count_and_ffmpeg_exit_status",
    }
    if is_refined:
        config["license"] = "Apache-2.0"
        config["confidence"] = "refined_ncc_to_seed_template_clamped_or_base_x_0.7_on_reject"
        assert ref_spec is not None
        if tracker_name == "csrt+hough":
            config["refinement"] = {
                "variant": f"opencv-{tracker_name}",
                "method": "hough",
                "hough_algorithm": "HOUGH_GRADIENT_ALT",
                "dp": 1.5,
                "minDist": "r_prev",
                "param1": 300,
                "param2": 0.9,
                "minRadius": "floor(0.85 * r_prev)",
                "maxRadius": "ceil(1.15 * r_prev)",
                "frame_to_frame_radius_max_fraction": 0.10,
                "seed_anchored_radius_max_fraction": 0.20,
                "rejected_fit_confidence_multiplier": 0.7,
                "reinit_period": None,
            }
        else:
            config["refinement"] = {
                "variant": f"opencv-{tracker_name}",
                "method": "circle",
                "roi_expansion": 1.3,
                "gaussian_blur": {"ksize": [0, 0], "sigma": 1.5},
                "canny_thresholds": "median_derived_0.66_1.33",
                "annulus_fraction": [0.75, 1.25],
                "ransac_iterations": 1000,
                "ransac_rng": "np.random.default_rng([42, frame_index])",
                "degenerate_circumradius_ratio": 10.0,
                "inlier_tolerance_px": 1.5,
                "refit": "taubin_algebraic_recomputed_inliers",
                "coverage_min_bins_of_36": 18,
                "frame_to_frame_radius_max_fraction": 0.10,
                "seed_anchored_radius_max_fraction": 0.20,
                "rejected_fit_confidence_multiplier": 0.7,
                "reinit_period": ref_spec["reinit_period"],
            }
    elif is_point_motion:
        config["license"] = "Apache-2.0"
        config["confidence"] = (
            "ransac_inliers_fraction_clamped"
            if tracker_name == "lk-affine"
            else "lk_inliers_fraction_or_csrt_x_0.7_on_fallback"
        )
        config["point_motion"] = {
            "query_annulus_fraction": [0.25, 0.90],
            "good_features_to_track": {
                "maxCorners": 200,
                "qualityLevel": 0.01,
                "minDistance": 5,
                "blockSize": 7,
            },
            "pyr_lk": {
                "winSize": [21, 21],
                "maxLevel": 3,
                "max_fb_error_px": 1.0,
            },
            "ransac_similarity": {
                "method": "cv2.RANSAC",
                "ransacReprojThreshold": 1.5,
                "maxIters": 2000,
                "confidence": 0.999,
                "refineIters": 10,
                "min_inliers": 12,
            },
            "fallback": "csrt" if tracker_name == "csrt+lk" else None,
            "fallback_confidence_multiplier": 0.7 if tracker_name == "csrt+lk" else None,
        }
    elif tracker_name in MODELS:
        spec = MODELS[tracker_name]
        config["confidence"] = "model_tracking_score_clamped"
        config["license"] = spec["license"]
        config["models"] = [
            {
                "role": f["role"],
                "filename": f["filename"],
                "sha256": f["sha256"],
                "source": f["source"],
                "upstream": f["upstream"],
            }
            for f in spec["files"]
        ]
    else:
        config["confidence"] = "ncc_to_seed_template_clamped"
        config["license"] = "Apache-2.0"

    prediction: dict[str, Any] = {
        "schema_version": 1,
        "fixture_id": fixture_id,
        "source_video_sha256": fixture["media"]["sha256"],
        "coordinate_space": "decoded_display_pixels",
        "implementation": {
            "name": f"opencv-{tracker_name}",
            "version": SPIKE_VERSION,
            "config": config,
        },
        "runtime": {"processing_wall_s": processing_wall_s},
        "samples": samples,
    }

    sidecar_doc: dict[str, Any] | None = None
    if is_refined or (tracker_name == "csrt+lk"):
        sidecar_doc = {
            "format": "openbar-research-geometry-sidecar",
            "format_version": 0,
            "fixture_id": fixture_id,
            "implementation": {
                "name": f"opencv-{tracker_name}",
                "version": SPIKE_VERSION,
                "config": config,
            },
            "samples": sidecar_samples,
        }

    return prediction, sidecar_doc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--tracker", choices=ALL_TRACKERS, required=True)
    parser.add_argument("--end-s", type=float, help="last timestamp to track (default: end of clip)")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-held-out", action="store_true", help="only for the frozen held-out evaluation")
    args = parser.parse_args(argv)
    try:
        prediction, sidecar_doc = track(args.manifest, args.fixture, args.seed, args.tracker, args.end_s, args.allow_held_out)
    except (SpikeError, label_package.PackageError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(prediction, indent=2) + "\n", encoding="utf-8")
    if sidecar_doc is not None:
        stem = args.output.name
        if stem.endswith(".prediction-v1.json"):
            sidecar_name = stem.removesuffix(".prediction-v1.json") + ".geometry.json"
        elif stem.endswith(".json"):
            sidecar_name = stem.removesuffix(".json") + ".geometry.json"
        else:
            sidecar_name = stem + ".geometry.json"
        sidecar_path = args.output.parent / sidecar_name
        sidecar_path.write_text(json.dumps(sidecar_doc, indent=2) + "\n", encoding="utf-8")
    tracked = sum(s["state"] == "tracked" for s in prediction["samples"])
    print(f"{prediction['implementation']['name']}: {tracked}/{len(prediction['samples'])} tracked, "
          f"{prediction['runtime']['processing_wall_s']} s -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
