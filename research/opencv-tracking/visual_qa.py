#!/usr/bin/env python3
"""Visual QA overlay generator for #57 tracker evaluation.

Draws the ground-truth annotation (yellow circle and centre) and each tracker's
prediction (distinct color crosshair) on crops around the plate on labelled frames,
tiling them into a composite visual QA image per clip.

Adheres to ADR-0006 (FFmpeg decode contract) and ADR-0007 (display-space pixel convention).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "tools"))
import label_package  # noqa: E402
import tracker_filter_selection as tfs  # noqa: E402

from track import decode_frames, load_inputs  # noqa: E402

# Distinct BGR colours for overlays
COLOR_GROUND_TRUTH = (0, 255, 255)  # Yellow
TRACKER_PALETTE = [
    (255, 255, 0),    # Cyan (csrt)
    (0, 255, 0),      # Bright Green (kcf)
    (255, 0, 255),    # Magenta (template-sad)
    (0, 140, 255),    # Orange (contrast-centroid)
    (0, 0, 255),      # Red
    (255, 200, 100),  # Sky blue
    (180, 105, 255),  # Pink
]


def extract_crops(
    manifest_path: Path,
    fixture_id: str,
    predictions_dir: Path,
    allow_held_out: bool,
    crop_size: int | None = None,
    prediction_files: list[Path] | None = None,
) -> tuple[list[np.ndarray], list[tuple[str, tuple[int, int, int]]]]:
    """Decode video, draw annotations and predictions, and extract cropped tiles."""
    if crop_size is not None and crop_size <= 0:
        raise SystemExit("error: --crop-size must be greater than zero")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    fixture = next((f for f in manifest["fixtures"] if f["id"] == fixture_id), None)
    if fixture is None:
        raise SystemExit(f"error: fixture {fixture_id} not found in manifest")
    if fixture["purpose"] == "validation" and not allow_held_out:
        raise SystemExit(f"error: {fixture_id} is a held-out validation fixture")

    annotations_path = manifest_path.parent / "annotations" / f"{fixture_id}.annotation-v1.json"
    if not annotations_path.is_file():
        raise SystemExit(f"error: missing annotations file {annotations_path}")
    annotation = json.loads(annotations_path.read_text(encoding="utf-8"))

    seed_path = manifest_path.parent / "seeds" / f"{fixture_id}.manual-target-seed-v1.json"
    if not seed_path.is_file():
        raise SystemExit(f"error: missing seed file {seed_path}")

    _, media, _ = load_inputs(manifest_path, fixture_id, seed_path, allow_held_out)
    probed = label_package.probe(media)
    label_package.require_fixture_probe_match(fixture, probed)
    width, height = label_package.display_size(probed["width_px"], probed["height_px"], probed["rotation_deg"])
    timestamps = probed["timestamps_s"]

    # Standalone use may discover files in the directory. Orchestrated comparisons pass the exact
    # files produced by that invocation so stale outputs from an older run cannot enter visual QA.
    if prediction_files is None:
        prediction_files = sorted(predictions_dir.glob(f"{fixture_id}.*.prediction-v1.json"))
    else:
        prediction_files = [path.resolve() for path in prediction_files]
        outside = [path for path in prediction_files if path.parent != predictions_dir.resolve()]
        if outside:
            raise SystemExit("error: --prediction files must be inside --predictions-dir")
        wrong_fixture = [
            path for path in prediction_files
            if not path.name.startswith(f"{fixture_id}.") or not path.name.endswith(".prediction-v1.json")
        ]
        if wrong_fixture:
            raise SystemExit("error: --prediction file does not match --fixture")
    if not prediction_files:
        raise SystemExit(f"error: no predictions found in {predictions_dir} for fixture {fixture_id}")
    missing = [path for path in prediction_files if not path.is_file()]
    if missing:
        raise SystemExit("error: prediction file is missing: " + ", ".join(map(str, missing)))

    trackers: list[dict[str, Any]] = []
    for p_path in prediction_files:
        p_doc = json.loads(p_path.read_text(encoding="utf-8"))
        name = p_doc.get("implementation", {}).get("name", p_path.stem)
        trackers.append({
            "name": name,
            "samples": p_doc.get("samples", []),
        })

    # Color mapping
    legend_items: list[tuple[str, tuple[int, int, int]]] = [("Ground Truth", COLOR_GROUND_TRUTH)]
    tracker_colors: dict[str, tuple[int, int, int]] = {}
    for idx, tr in enumerate(trackers):
        c = TRACKER_PALETTE[idx % len(TRACKER_PALETTE)]
        tracker_colors[tr["name"]] = c
        legend_items.append((tr["name"], c))

    tol = float(annotation.get("timebase", {}).get("decoder_match_tolerance_s", 0.0005))
    labelled_samples = [
        s for s in annotation.get("samples", [])
        if s.get("annotation_state") == "labelled" and s.get("quality") != "unusable"
    ]
    labelled_by_time = {s["timestamp_s"]: s for s in labelled_samples}

    # Pre-match predictions to labelled samples
    matched_preds: dict[str, dict[float, dict[str, Any] | None]] = {}
    for tr in trackers:
        matches = tfs._match_timestamped_samples(labelled_samples, tr["samples"], tol)
        matched_preds[tr["name"]] = {ref["timestamp_s"]: act for ref, act in matches}

    # Find which frame indices correspond to labelled samples
    labelled_frame_indices: dict[int, dict[str, Any]] = {}
    for ref in labelled_samples:
        t_ref = ref["timestamp_s"]
        best_i = min(range(len(timestamps)), key=lambda i: abs(timestamps[i] - t_ref))
        delta = abs(timestamps[best_i] - t_ref)
        if delta > tol:
            raise SystemExit(
                f"error: labelled timestamp {t_ref} has no decoded frame within {tol} s "
                f"(nearest delta {delta:.9f} s)"
            )
        if best_i in labelled_frame_indices:
            raise SystemExit(
                f"error: multiple labelled samples map to decoded frame {best_i}; "
                "visual QA requires one-to-one timestamp alignment"
            )
        labelled_frame_indices[best_i] = ref

    # Determine default crop size if not provided
    if crop_size is None:
        radii = [s.get("target_size_px", {}).get("radius_px", 90.0) for s in labelled_samples]
        avg_radius = float(np.mean(radii)) if radii else 90.0
        crop_size = int(round(avg_radius * 2.8))
        crop_size = max(160, min(crop_size, 400))
        # make it even
        if crop_size % 2 != 0:
            crop_size += 1

    half_crop = crop_size // 2
    crops: list[np.ndarray] = []

    for index, frame in enumerate(decode_frames(media, width, height, len(timestamps))):
        if index not in labelled_frame_indices:
            continue

        ref = labelled_frame_indices[index]
        t = ref["timestamp_s"]
        gt_cx = float(ref["center_px"]["x_px"])
        gt_cy = float(ref["center_px"]["y_px"])
        gt_r = float(ref.get("target_size_px", {}).get("radius_px", 90.0))

        # Crop window around ground truth center
        ix = int(round(gt_cx))
        iy = int(round(gt_cy))
        x0, y0 = ix - half_crop, iy - half_crop
        x1, y1 = x0 + crop_size, y0 + crop_size

        # Padded crop handling near boundary
        pad_left = max(0, -x0)
        pad_top = max(0, -y0)
        pad_right = max(0, x1 - width)
        pad_bottom = max(0, y1 - height)

        src_x0, src_y0 = max(0, x0), max(0, y0)
        src_x1, src_y1 = min(width, x1), min(height, y1)

        patch = frame[src_y0:src_y1, src_x0:src_x1]
        if pad_left > 0 or pad_top > 0 or pad_right > 0 or pad_bottom > 0:
            patch = cv2.copyMakeBorder(
                patch, pad_top, pad_bottom, pad_left, pad_right,
                cv2.BORDER_CONSTANT, value=(0, 0, 0)
            )

        # Coordinate transformation into crop space
        # (x_frame, y_frame) -> (x_frame - x0, y_frame - y0)
        crop_draw = patch.copy()

        # 1. Draw ground truth circle and center
        gt_crop_cx = gt_cx - x0
        gt_crop_cy = gt_cy - y0
        cv2.circle(crop_draw, (int(round(gt_crop_cx)), int(round(gt_crop_cy))),
                   int(round(gt_r)), COLOR_GROUND_TRUTH, 2, cv2.LINE_AA)
        cv2.circle(crop_draw, (int(round(gt_crop_cx)), int(round(gt_crop_cy))),
                   2, COLOR_GROUND_TRUTH, -1, cv2.LINE_AA)

        # 2. Draw tracker predictions
        for tr in trackers:
            name = tr["name"]
            color = tracker_colors[name]
            act = matched_preds[name].get(t)
            if act is not None and act.get("state") == "tracked":
                c_pred = act.get("center_px")
                if c_pred:
                    px = c_pred["x_px"] - x0
                    py = c_pred["y_px"] - y0
                    cv2.drawMarker(
                        crop_draw,
                        (int(round(px)), int(round(py))),
                        color,
                        markerType=cv2.MARKER_CROSS,
                        markerSize=10,
                        thickness=2,
                        line_type=cv2.LINE_AA,
                    )

        # 3. Add timestamp label banner on crop
        label_text = f"t={t:.3f}s"
        cv2.rectangle(crop_draw, (0, 0), (80, 20), (0, 0, 0), -1)
        cv2.putText(crop_draw, label_text, (4, 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1, cv2.LINE_AA)

        crops.append(crop_draw)

    return crops, legend_items


def tile_crops(
    crops: list[np.ndarray],
    legend_items: list[tuple[str, tuple[int, int, int]]],
    title: str,
    cols: int = 5,
) -> np.ndarray:
    """Tile crops into a single visual QA composite image with a header and legend."""
    if not crops:
        raise SystemExit("error: no crops to tile")

    n_crops = len(crops)
    cols = min(cols, n_crops)
    rows = math.ceil(n_crops / cols)
    crop_h, crop_w, _ = crops[0].shape

    # Calculate legend bar height
    header_h = 70
    grid_w = cols * crop_w
    grid_h = rows * crop_h
    total_w = grid_w
    total_h = header_h + grid_h

    canvas = np.zeros((total_h, total_w, 3), dtype=np.uint8)

    # 1. Draw Title
    cv2.putText(canvas, title, (15, 25),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)

    # 2. Draw Legend Swatches
    legend_x = 15
    legend_y = 50
    for name, color in legend_items:
        # circle or rectangle marker
        if name == "Ground Truth":
            cv2.circle(canvas, (legend_x + 8, legend_y), 6, color, 2, cv2.LINE_AA)
            cv2.circle(canvas, (legend_x + 8, legend_y), 2, color, -1, cv2.LINE_AA)
        else:
            cv2.drawMarker(canvas, (legend_x + 8, legend_y), color,
                           markerType=cv2.MARKER_CROSS, markerSize=12, thickness=2, line_type=cv2.LINE_AA)
        cv2.putText(canvas, name, (legend_x + 22, legend_y + 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (220, 220, 220), 1, cv2.LINE_AA)
        item_w = 26 + int(len(name) * 8.5) + 15
        legend_x += item_w

    # 3. Blit Crops into Grid
    for idx, crop in enumerate(crops):
        r = idx // cols
        c = idx % cols
        y_start = header_h + r * crop_h
        x_start = c * crop_w
        canvas[y_start:y_start + crop_h, x_start:x_start + crop_w] = crop
        # draw subtle border around each tile
        cv2.rectangle(canvas, (x_start, y_start), (x_start + crop_w - 1, y_start + crop_h - 1),
                      (60, 60, 60), 1)

    return canvas


def generate_visual_qa(
    manifest_path: Path,
    fixture_id: str,
    predictions_dir: Path,
    output_path: Path,
    allow_held_out: bool,
    crop_size: int | None = None,
    cols: int = 5,
    prediction_files: list[Path] | None = None,
) -> Path:
    if cols <= 0:
        raise SystemExit("error: --cols must be greater than zero")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    fixture = next((f for f in manifest["fixtures"] if f["id"] == fixture_id), None)
    if fixture is None:
        raise SystemExit(f"error: fixture {fixture_id} not found in manifest")
    # Visual QA contains decoded/cropped source frames, so non-redistributable footage must
    # remain below validation/private rather than merely relying on target/ being git-ignored.
    try:
        label_package.require_safe_output(fixture, output_path.parent)
    except label_package.PackageError as error:
        raise SystemExit(f"error: {error}") from error

    crops, legend = extract_crops(
        manifest_path,
        fixture_id,
        predictions_dir,
        allow_held_out,
        crop_size=crop_size,
        prediction_files=prediction_files,
    )
    tiled = tile_crops(crops, legend, title=f"Tracker Visual QA: {fixture_id}", cols=cols)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output_path), tiled):
        raise SystemExit(f"error: OpenCV failed to write visual QA image to {output_path}")
    return output_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--predictions-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--prediction",
        type=Path,
        action="append",
        dest="predictions",
        help="exact prediction file to overlay; repeatable (default: discover all matching files)",
    )
    parser.add_argument("--crop-size", type=int, help="crop size in pixels (default: proportional to plate radius)")
    parser.add_argument("--cols", type=int, default=5, help="number of columns in tiled grid (default: 5)")
    parser.add_argument("--allow-held-out", action="store_true")
    args = parser.parse_args(argv)

    out = generate_visual_qa(
        args.manifest,
        args.fixture,
        args.predictions_dir,
        args.output,
        args.allow_held_out,
        crop_size=args.crop_size,
        cols=args.cols,
        prediction_files=args.predictions,
    )
    print(f"Visual QA saved: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
