#!/usr/bin/env python3
"""Phase B student detector training and validation harness (#233 Phase B).

Trains an Apache-2.0 single-class plate detector (``D-FINE-S``, ``ustc-community/dfine-small-coco``,
zero SAM 3 weights) on a curated Phase B COCO dataset produced by ``distill_dataset.py``, and scores
validation predictions on the session-disjoint ``val`` fold under ``identity-loose-v1``.

Pure validation, format conversion, and evaluation helpers are stdlib/numpy only and unit-tested
without loading model weights. Training imports ``torch`` and ``transformers`` lazily.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Sequence

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "validation" / "tools"))

import clips  # noqa: E402
import detectors  # noqa: E402
import geometry  # noqa: E402
import label_package  # noqa: E402
import selection  # noqa: E402

HARNESS_ID = "plate-detection-phase-b-train-v2"
STUDENT_CLASS_ID = 0
STUDENT_CLASS_NAME = "barbell_plate"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _percentile90(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, math.ceil(0.90 * len(ordered)) - 1))
    return round(ordered[idx], 4)


# ---------------------------------------------------------------------------------------------
# Training-time augmentation (``student-aug-v1``). Applied to the train fold only, never to val.
# Each sample draws from ``numpy.random.default_rng([seed, epoch, index])`` so a run is
# reproducible for a given seed (up to GPU kernel nondeterminism). Boxes are COCO
# ``[x_min, y_min, width, height]`` in pixel-edge coordinates (``[0, width] x [0, height]``,
# ADR-0007 centre coordinates shifted by +0.5 px), so crops are exact integer shifts and the
# horizontal mirror is ``x' = width - x - w``.
# ---------------------------------------------------------------------------------------------
AUGMENT_NONE = "none"
AUGMENT_V1 = "student-aug-v1"
AUGMENT_V1_CONFIG: dict[str, Any] = {
    "crop": {"p": 0.5, "scale_range": [0.6, 1.0], "keeps_full_box": True, "keeps_aspect": True},
    "hflip": {"p": 0.5},
    "colour": {"p": 0.8, "brightness_range": [0.7, 1.3], "contrast_range": [0.7, 1.3],
               "saturation_range": [0.7, 1.3], "hue_shift_max_deg": 10.0},
    "motion_blur": {"p": 0.3, "kernel_px_range": [3, 15], "angle_deg_range": [0.0, 180.0]},
    "jpeg": {"p": 0.3, "quality_range": [30, 90]},
    "order": ["crop", "hflip", "colour", "motion_blur", "jpeg"],
}
AUGMENT_CONFIGS: dict[str, dict[str, Any] | None] = {AUGMENT_NONE: None, AUGMENT_V1: AUGMENT_V1_CONFIG}
LR_SCHEDULES = ("constant", "cosine")


def _check_bbox(bbox: Sequence[float], width_px: int, height_px: int) -> list[float]:
    box = [float(v) for v in bbox]
    if len(box) != 4 or not all(math.isfinite(v) for v in box):
        raise ValueError(f"bbox must be 4 finite numbers: {bbox!r}")
    if box[2] <= 0.0 or box[3] <= 0.0:
        raise ValueError(f"bbox must have positive size: {bbox!r}")
    if width_px <= 0 or height_px <= 0:
        raise ValueError(f"image size must be positive: {width_px}x{height_px}")
    return box


def hflip_bbox(bbox: Sequence[float], width_px: int) -> list[float]:
    """Mirror a pixel-edge COCO box horizontally inside an image of ``width_px`` columns."""
    x, y, w, h = _check_bbox(bbox, width_px, 1)
    return [float(width_px) - x - w, y, w, h]


def crop_window_containing(
    bbox: Sequence[float], width_px: int, height_px: int, scale: float, u: float, v: float
) -> tuple[int, int, int, int] | None:
    """Return an integer crop ``(x0, y0, crop_w, crop_h)`` of ``scale`` x the frame that holds ``bbox``.

    ``u`` and ``v`` in ``[0, 1]`` pick the offset inside the admissible range. The crop keeps the image
    aspect ratio; when ``scale`` is too small to hold the whole box it grows to the smallest scale that
    does. Returns ``None`` when no crop smaller than the frame can contain the box.
    """
    x, y, w, h = _check_bbox(bbox, width_px, height_px)
    if not (math.isfinite(scale) and 0.0 < scale <= 1.0):
        raise ValueError(f"scale must be in (0, 1]: {scale!r}")
    if not (0.0 <= u <= 1.0 and 0.0 <= v <= 1.0):
        raise ValueError(f"u and v must be in [0, 1]: {u!r}, {v!r}")
    x0_box, y0_box = math.floor(x), math.floor(y)
    x1_box, y1_box = math.ceil(x + w), math.ceil(y + h)
    if x0_box < 0 or y0_box < 0 or x1_box > width_px or y1_box > height_px:
        return None  # a box touching outside the frame cannot be held whole by any crop
    need = max((x1_box - x0_box) / width_px, (y1_box - y0_box) / height_px)
    scale = max(scale, need)
    crop_w = min(width_px, math.ceil(width_px * scale))
    crop_h = min(height_px, math.ceil(height_px * scale))
    if crop_w >= width_px and crop_h >= height_px:
        return None
    lo_x, hi_x = max(0, x1_box - crop_w), min(x0_box, width_px - crop_w)
    lo_y, hi_y = max(0, y1_box - crop_h), min(y0_box, height_px - crop_h)
    if lo_x > hi_x or lo_y > hi_y:
        return None
    x0 = lo_x + int(math.floor(u * (hi_x - lo_x) + 0.5))
    y0 = lo_y + int(math.floor(v * (hi_y - lo_y) + 0.5))
    return x0, y0, crop_w, crop_h


def _motion_blur_kernel(length_px: int, angle_deg: float) -> Any:
    import cv2
    import numpy as np

    kernel = np.zeros((length_px, length_px), dtype=np.float32)
    kernel[length_px // 2, :] = 1.0
    centre = ((length_px - 1) / 2.0, (length_px - 1) / 2.0)
    rot = cv2.getRotationMatrix2D(centre, angle_deg, 1.0)
    kernel = cv2.warpAffine(kernel, rot, (length_px, length_px), flags=cv2.INTER_LINEAR)
    total = float(kernel.sum())
    if total <= 0.0:
        kernel[length_px // 2, length_px // 2] = 1.0
        total = 1.0
    return kernel / total


def augment_sample(
    image_rgb: Any, bbox: Sequence[float], rng: Any, config: dict[str, Any] = AUGMENT_V1_CONFIG
) -> tuple[Any, list[float], list[str]]:
    """Apply ``student-aug-v1`` to an ``HxWx3`` uint8 RGB array and its single pixel-edge COCO box.

    Returns the new image, the transformed box, and the names of the operations applied (in order).
    The input array is never modified in place.
    """
    import cv2
    import numpy as np

    image = np.ascontiguousarray(image_rgb)
    if image.ndim != 3 or image.shape[2] != 3 or image.dtype != np.uint8:
        raise ValueError("image_rgb must be an HxWx3 uint8 array")
    height_px, width_px = int(image.shape[0]), int(image.shape[1])
    box = _check_bbox(bbox, width_px, height_px)
    applied: list[str] = []

    crop = config["crop"]
    if rng.random() < crop["p"]:
        lo, hi = crop["scale_range"]
        window = crop_window_containing(
            box, width_px, height_px, float(rng.uniform(lo, hi)), float(rng.random()), float(rng.random())
        )
        if window is not None:
            x0, y0, cw, ch = window
            image = image[y0 : y0 + ch, x0 : x0 + cw]
            box = [box[0] - x0, box[1] - y0, box[2], box[3]]
            height_px, width_px = ch, cw
            applied.append("crop")

    if rng.random() < config["hflip"]["p"]:
        image = image[:, ::-1]
        box = hflip_bbox(box, width_px)
        applied.append("hflip")

    colour = config["colour"]
    if rng.random() < colour["p"]:
        brightness = float(rng.uniform(*colour["brightness_range"]))
        contrast = float(rng.uniform(*colour["contrast_range"]))
        saturation = float(rng.uniform(*colour["saturation_range"]))
        hue_deg = float(rng.uniform(-colour["hue_shift_max_deg"], colour["hue_shift_max_deg"]))
        work = image.astype(np.float32) * brightness
        mean = float(work.mean())
        work = (work - mean) * contrast + mean
        work = np.clip(work, 0.0, 255.0).astype(np.uint8)
        hsv = cv2.cvtColor(work, cv2.COLOR_RGB2HSV).astype(np.float32)
        hsv[..., 0] = np.mod(hsv[..., 0] + hue_deg / 2.0, 180.0)  # OpenCV hue unit is 2 degrees
        hsv[..., 1] = np.clip(hsv[..., 1] * saturation, 0.0, 255.0)
        image = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2RGB)
        applied.append("colour")

    blur = config["motion_blur"]
    if rng.random() < blur["p"]:
        k_lo, k_hi = blur["kernel_px_range"]
        length = int(rng.integers(k_lo, k_hi + 1)) | 1  # odd kernel
        angle = float(rng.uniform(*blur["angle_deg_range"]))
        image = cv2.filter2D(np.ascontiguousarray(image), -1, _motion_blur_kernel(length, angle),
                             borderType=cv2.BORDER_REFLECT)
        applied.append("motion_blur")

    jpeg = config["jpeg"]
    if rng.random() < jpeg["p"]:
        q_lo, q_hi = jpeg["quality_range"]
        quality = int(rng.integers(q_lo, q_hi + 1))
        ok, buf = cv2.imencode(".jpg", np.ascontiguousarray(image[:, :, ::-1]), [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            raise RuntimeError("JPEG re-encoding failed during augmentation")
        image = cv2.imdecode(buf, cv2.IMREAD_COLOR)[:, :, ::-1]
        applied.append("jpeg")

    return np.ascontiguousarray(image), [float(v) for v in box], applied


def cosine_lr_factor(step: int, total_steps: int, min_factor: float = 0.05) -> float:
    """Cosine decay of the learning-rate multiplier from 1.0 to ``min_factor`` over ``total_steps``."""
    if total_steps <= 0:
        raise ValueError("total_steps must be positive")
    if not 0.0 <= min_factor <= 1.0:
        raise ValueError("min_factor must be in [0, 1]")
    progress = min(1.0, max(0.0, step / total_steps))
    return min_factor + (1.0 - min_factor) * 0.5 * (1.0 + math.cos(math.pi * progress))


def validate_dataset_dir(
    dataset_dir: Path,
    repo_root: Path,
    manifests: list[Path] | None = None,
) -> dict[str, Any]:
    """Fail-closed admission check on a curated Phase B dataset directory before training or eval."""
    dataset_dir = dataset_dir.resolve()
    repo_root = repo_root.resolve()
    if not dataset_dir.is_relative_to(label_package.PRIVATE_ROOT.resolve()):
        raise SystemExit(f"dataset_dir must be under {label_package.PRIVATE_ROOT}")

    manifest_file = dataset_dir / "dataset_manifest.json"
    train_file = dataset_dir / "annotations" / "train.coco.json"
    val_file = dataset_dir / "annotations" / "val.coco.json"
    for req in (manifest_file, train_file, val_file):
        if not req.is_file():
            raise FileNotFoundError(f"missing required Phase B dataset file: {req}")

    dataset_manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    train_coco = json.loads(train_file.read_text(encoding="utf-8"))
    val_coco = json.loads(val_file.read_text(encoding="utf-8"))

    manifest_paths = manifests or [
        repo_root / "validation" / "private" / "manifest.json",
        repo_root / "validation" / "private" / "vbt" / "manifest.json",
    ]
    fixtures = clips.load_fixtures(manifest_paths)

    for clip in dataset_manifest.get("clips") or []:
        cid = clip.get("fixture_id", "<missing>")
        role = clip.get("role")
        if cid not in fixtures or clips.development_role(fixtures[cid]) != role:
            raise clips.ClipRefused(f"{cid}: missing fixture or development-role mismatch")

    for fold_name, coco in (("train", train_coco), ("val", val_coco)):
        for img in coco.get("images") or []:
            cid = img.get("fixture_id", "<missing>")
            if cid not in fixtures:
                raise clips.ClipRefused(f"{cid} in {fold_name}.coco.json is missing from manifests")
            clips.development_role(fixtures[cid])

    train_sessions = {img["session_group"] for img in train_coco.get("images") or []}
    val_sessions = {img["session_group"] for img in val_coco.get("images") or []}
    overlap = train_sessions & val_sessions
    if overlap:
        raise RuntimeError(f"session group leakage between train and val COCO splits: {sorted(overlap)}")

    return {
        "dataset_manifest": dataset_manifest,
        "train_coco": train_coco,
        "val_coco": val_coco,
        "hashes": {
            "dataset_manifest_sha256": sha256_file(manifest_file),
            "train_coco_sha256": sha256_file(train_file),
            "val_coco_sha256": sha256_file(val_file),
        },
    }


def coco_to_hf_annotations(
    image_entry: dict[str, Any],
    anns: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Convert a COCO image entry and its annotations to Hugging Face AutoImageProcessor format."""
    image_id = int(image_entry["id"])
    hf_anns: list[dict[str, Any]] = []
    for ann in anns:
        bbox = [float(v) for v in ann["bbox"]]
        if len(bbox) != 4 or not all(math.isfinite(v) for v in bbox):
            raise ValueError(f"non-finite bbox on image_id={image_id}: {bbox!r}")
        if bbox[2] <= 0.0 or bbox[3] <= 0.0:
            raise ValueError(f"non-positive bbox dimensions on image_id={image_id}: {bbox!r}")
        hf_anns.append(
            {
                "bbox": bbox,
                "category_id": STUDENT_CLASS_ID,
                "area": float(ann.get("area", bbox[2] * bbox[3])),
                "iscrowd": int(ann.get("iscrowd", 0)),
            }
        )
    return {"image_id": image_id, "annotations": hf_anns}


def evaluate_box_predictions(
    predictions_by_image_id: dict[int, Sequence[dict[str, Any]]],
    val_coco: dict[str, Any],
) -> dict[str, Any]:
    """Score per-image candidate rows against a Phase B COCO validation split."""
    images_by_id = {int(img["id"]): img for img in val_coco.get("images") or []}
    anns_by_image_id: dict[int, list[dict[str, Any]]] = {img_id: [] for img_id in images_by_id}
    for ann in val_coco.get("annotations") or []:
        img_id = int(ann["image_id"])
        if img_id in anns_by_image_id:
            anns_by_image_id[img_id].append(ann)

    total_frames = 0
    recall_hits = 0
    top1_hits = 0
    plausible_top1_hits = 0
    refused_count = 0
    accepted_count = 0
    accepted_correct = 0
    ious: list[float] = []
    centre_errs_rel: list[float] = []
    radius_errs_rel: list[float] = []

    for img_id in sorted(images_by_id):
        gt_anns = anns_by_image_id.get(img_id) or []
        if not gt_anns:
            continue
        gt = gt_anns[0]
        gt_cx = float(gt["center_x_px"])
        gt_cy = float(gt["center_y_px"])
        gt_r = float(gt["radius_px"])
        gt_xyxy = [
            float(gt["bbox"][0]),
            float(gt["bbox"][1]),
            float(gt["bbox"][0] + gt["bbox"][2]),
            float(gt["bbox"][1] + gt["bbox"][3]),
        ]
        img_meta = images_by_id[img_id]
        width_px = int(img_meta["width"])
        height_px = int(img_meta["height"])

        rows = list(predictions_by_image_id.get(img_id) or [])
        cands = [selection.Candidate.from_dict(r) for r in rows]
        window = selection.in_frame(selection.plausible(cands, width_px, height_px), width_px, height_px)
        refusal = selection.margin_refusal(window)

        total_frames += 1
        top_raw = selection.select_top1(cands)
        top_plausible = selection.select_top1(window)

        if top_raw is not None and selection.match(top_raw, gt_cx, gt_cy, gt_r):
            top1_hits += 1
        plausible_ok = top_plausible is not None and selection.match(top_plausible, gt_cx, gt_cy, gt_r)
        if plausible_ok:
            plausible_top1_hits += 1

        if refusal["refused"]:
            refused_count += 1
        else:
            accepted_count += 1
            if plausible_ok:
                accepted_correct += 1

        matched_rows = [
            (r, selection.Candidate.from_dict(r))
            for r in rows
            if selection.match(selection.Candidate.from_dict(r), gt_cx, gt_cy, gt_r)
        ]
        if matched_rows:
            recall_hits += 1
            best_row, best_cand = min(
                matched_rows,
                key=lambda item: math.hypot(item[1].center_x_px - gt_cx, item[1].center_y_px - gt_cy),
            )
            centre_errs_rel.append(
                math.hypot(best_cand.center_x_px - gt_cx, best_cand.center_y_px - gt_cy) / gt_r
            )
            radius_errs_rel.append(abs(best_cand.radius_px - gt_r) / gt_r)
            if "box_xyxy" in best_row:
                ious.append(detectors.box_iou([float(v) for v in best_row["box_xyxy"]], gt_xyxy))
            else:
                pred_xyxy = [
                    best_cand.center_x_px + 0.5 - best_cand.radius_px,
                    best_cand.center_y_px + 0.5 - best_cand.radius_px,
                    best_cand.center_x_px + 0.5 + best_cand.radius_px,
                    best_cand.center_y_px + 0.5 + best_cand.radius_px,
                ]
                ious.append(detectors.box_iou(pred_xyxy, gt_xyxy))

    return {
        "evaluated_frames": total_frames,
        "frame_recall": recall_hits,
        "frame_recall_rate": round(recall_hits / total_frames, 4) if total_frames else None,
        "frame_top1": top1_hits,
        "frame_top1_rate": round(top1_hits / total_frames, 4) if total_frames else None,
        "frame_plausible_top1": plausible_top1_hits,
        "frame_plausible_top1_rate": round(plausible_top1_hits / total_frames, 4) if total_frames else None,
        "refusal": {
            "rule": selection.MARGIN_RULE,
            "refused": refused_count,
            "accepted": accepted_count,
            "accepted_correct": accepted_correct,
        },
        "box_iou_median": round(statistics.median(ious), 4) if ious else None,
        "box_iou_p90": _percentile90(ious),
        "centre_error_rel_median": round(statistics.median(centre_errs_rel), 4) if centre_errs_rel else None,
        "centre_error_rel_p90": _percentile90(centre_errs_rel),
        "radius_abs_error_rel_median": round(statistics.median(radius_errs_rel), 4) if radius_errs_rel else None,
        "radius_abs_error_rel_p90": _percentile90(radius_errs_rel),
    }


def _linear_sum_assignment(cost_matrix: Any) -> tuple[Any, Any]:
    """Exact minimum-weight bipartite assignment (Hungarian algorithm) for m x n cost matrices.

    Used when ``scipy.optimize.linear_sum_assignment`` is not installed in the environment.
    """
    import numpy as np

    cost = np.asarray(cost_matrix, dtype=np.float64)
    if cost.ndim != 2:
        raise ValueError(f"expected 2D cost matrix, got shape {cost.shape}")
    nrows, ncols = cost.shape
    if nrows == 0 or ncols == 0:
        empty = np.empty(0, dtype=np.int64)
        return empty, empty

    transposed = False
    if nrows > ncols:
        cost = cost.T
        nrows, ncols = ncols, nrows
        transposed = True

    # Classic O(nrows^2 * ncols) Dijkstra/Hungarian shortest augmenting path (1-indexed).
    u = np.zeros(nrows + 1, dtype=np.float64)
    v = np.zeros(ncols + 1, dtype=np.float64)
    p = np.zeros(ncols + 1, dtype=np.int64)
    way = np.zeros(ncols + 1, dtype=np.int64)

    for i in range(1, nrows + 1):
        p[0] = i
        j0 = 0
        minv = np.full(ncols + 1, np.inf, dtype=np.float64)
        used = np.zeros(ncols + 1, dtype=bool)
        while True:
            used[j0] = True
            i0 = p[j0]
            row = cost[i0 - 1]
            delta = np.inf
            j1 = 0
            for j in range(1, ncols + 1):
                if not used[j]:
                    cur = float(row[j - 1]) - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j] = cur
                        way[j] = j0
                    if minv[j] < delta:
                        delta = minv[j]
                        j1 = j
            for j in range(ncols + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = int(way[j0])
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break

    row_ind = np.arange(nrows, dtype=np.int64)
    col_ind = np.zeros(nrows, dtype=np.int64)
    for j in range(1, ncols + 1):
        if p[j] > 0:
            col_ind[p[j] - 1] = j - 1

    if transposed:
        order = np.argsort(col_ind)
        return col_ind[order], row_ind[order]
    return row_ind, col_ind


def train_dfine_student(
    *,
    dataset_dir: Path,
    repo_root: Path,
    output_dir: Path,
    base_checkpoint: str = detectors.DFINE_CHECKPOINT,
    epochs: int = 10,
    batch_size: int = 4,
    learning_rate: float = 1e-4,
    seed: int = 0,
    manifests: list[Path] | None = None,
    augment: str = AUGMENT_NONE,
    lr_schedule: str = "constant",
    eval_dataset_dir: Path | None = None,
) -> dict[str, Any]:
    """Fine-tune D-FINE-S on the curated train fold and evaluate on the session-disjoint val fold.

    ``eval_dataset_dir`` (optional) takes the val fold from another curated dataset, e.g. the
    stride-15 dataset, so runs trained on different strides are selected and reported on the
    same frames. Its val sessions must be disjoint from the training sessions.
    """
    if augment not in AUGMENT_CONFIGS:
        raise SystemExit(f"unknown --augment {augment!r}; choose from {sorted(AUGMENT_CONFIGS)}")
    if lr_schedule not in LR_SCHEDULES:
        raise SystemExit(f"unknown --lr-schedule {lr_schedule!r}; choose from {list(LR_SCHEDULES)}")
    if epochs < 1 or batch_size < 1 or not (math.isfinite(learning_rate) and learning_rate > 0.0):
        raise SystemExit("epochs and batch_size must be >= 1 and learning_rate must be a positive finite number")
    output_dir = output_dir.resolve()
    if not output_dir.is_relative_to(label_package.PRIVATE_ROOT.resolve()):
        raise SystemExit(f"output_dir must be under {label_package.PRIVATE_ROOT}")
    if output_dir.exists():
        raise SystemExit(f"{output_dir} already exists; choose a new --output-dir")

    validated = validate_dataset_dir(dataset_dir, repo_root, manifests=manifests)
    train_coco = validated["train_coco"]
    val_coco = validated["val_coco"]
    val_root = dataset_dir.resolve()
    eval_hashes: dict[str, Any] | None = None
    if eval_dataset_dir is not None:
        eval_validated = validate_dataset_dir(eval_dataset_dir, repo_root, manifests=manifests)
        train_sessions = {img["session_group"] for img in train_coco.get("images") or []}
        eval_sessions = {img["session_group"] for img in eval_validated["val_coco"].get("images") or []}
        if train_sessions & eval_sessions:
            raise RuntimeError(
                f"eval val fold shares session groups with the training fold: {sorted(train_sessions & eval_sessions)}"
            )
        val_coco = eval_validated["val_coco"]
        val_root = eval_dataset_dir.resolve()
        eval_hashes = {"path": val_root.name, **eval_validated["hashes"]}
    augment_config = AUGMENT_CONFIGS[augment]

    import cv2
    import numpy as np
    import torch
    from PIL import Image
    from torch.utils.data import DataLoader, Dataset
    from transformers import AutoImageProcessor, DFineForObjectDetection
    from transformers.loss import loss_rt_detr

    if not hasattr(loss_rt_detr, "linear_sum_assignment"):
        loss_rt_detr.linear_sum_assignment = _linear_sum_assignment
        loss_rt_detr.requires_backends = lambda *args, **kwargs: None

    torch.manual_seed(seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    processor = AutoImageProcessor.from_pretrained(base_checkpoint)
    model = DFineForObjectDetection.from_pretrained(
        base_checkpoint,
        num_labels=1,
        id2label={STUDENT_CLASS_ID: STUDENT_CLASS_NAME},
        label2id={STUDENT_CLASS_NAME: STUDENT_CLASS_ID},
        ignore_mismatched_sizes=True,
    ).to(device)

    class _CocoDataset(Dataset):
        def __init__(self, coco_dict: dict[str, Any], root: Path) -> None:
            self.images = list(coco_dict.get("images") or [])
            self.anns_by_id: dict[int, list[dict[str, Any]]] = {int(img["id"]): [] for img in self.images}
            for ann in coco_dict.get("annotations") or []:
                img_id = int(ann["image_id"])
                if img_id in self.anns_by_id:
                    self.anns_by_id[img_id].append(ann)
            self.root = root
            self.epoch = 0
            self.augment_counts: dict[str, int] = {}

        def __len__(self) -> int:
            return len(self.images)

        def __getitem__(self, idx: int) -> tuple[Image.Image, dict[str, Any]]:
            img_entry = self.images[idx]
            img_path = self.root / img_entry["file_name"]
            if not img_path.is_file():
                raise FileNotFoundError(
                    f"missing extracted frame {img_path}; run distill_dataset.py --extract-frames first"
                )
            pil = Image.open(img_path).convert("RGB")
            anns = self.anns_by_id[int(img_entry["id"])]
            if augment_config is not None and len(anns) == 1:
                rng = np.random.default_rng([seed, self.epoch, idx])
                arr, box, applied = augment_sample(np.asarray(pil), anns[0]["bbox"], rng, augment_config)
                pil = Image.fromarray(arr)
                anns = [{**anns[0], "bbox": box, "area": box[2] * box[3]}]
                for name in applied:
                    self.augment_counts[name] = self.augment_counts.get(name, 0) + 1
            target = coco_to_hf_annotations(img_entry, anns)
            return pil, target

    def _collate(batch: list[tuple[Image.Image, dict[str, Any]]]) -> dict[str, Any]:
        images = [item[0] for item in batch]
        annotations = [item[1] for item in batch]
        return processor(images=images, annotations=annotations, return_tensors="pt")

    train_ds = _CocoDataset(train_coco, dataset_dir.resolve())
    loader_gen = torch.Generator().manual_seed(seed)
    train_loader = DataLoader(
        train_ds, batch_size=batch_size, shuffle=True, generator=loader_gen, collate_fn=_collate
    )
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate, weight_decay=1e-4)
    total_steps = epochs * len(train_loader)
    global_step = 0

    output_dir.mkdir(parents=True, exist_ok=True)
    best_checkpoint_dir = output_dir / "best_checkpoint"
    best_recall = -1.0
    best_epoch: int | None = None
    history: list[dict[str, Any]] = []

    for epoch in range(1, epochs + 1):
        started = time.perf_counter()
        model.train()
        train_ds.epoch = epoch
        train_ds.augment_counts = {}
        total_loss = 0.0
        steps = 0
        for batch in train_loader:
            if lr_schedule == "cosine":
                for group in optimizer.param_groups:
                    group["lr"] = learning_rate * cosine_lr_factor(global_step, total_steps)
            pixel_values = batch["pixel_values"].to(device)
            labels = [
                {k: (v.to(device) if hasattr(v, "to") else v) for k, v in t.items()}
                for t in batch["labels"]
            ]
            optimizer.zero_grad()
            outputs = model(pixel_values=pixel_values, labels=labels)
            loss = outputs.loss
            loss.backward()
            optimizer.step()
            total_loss += float(loss.detach().cpu().item())
            steps += 1
            global_step += 1

        import rim_refine

        model.eval()
        preds_by_id: dict[int, list[dict[str, Any]]] = {}
        refined_preds_by_id: dict[int, list[dict[str, Any]]] = {}
        for img_entry in val_coco.get("images") or []:
            img_path = val_root / img_entry["file_name"]
            bgr = cv2.imread(str(img_path), cv2.IMREAD_COLOR)
            if bgr is None:
                raise FileNotFoundError(f"cannot read validation frame {img_path}")
            pil = Image.fromarray(bgr[:, :, ::-1].copy())
            inputs = processor(images=pil, return_tensors="pt").to(device)
            with torch.no_grad():
                out = model(**inputs)
            res = processor.post_process_object_detection(
                out, threshold=detectors.DFINE_SCORE_THRESHOLD, target_sizes=[(pil.size[1], pil.size[0])]
            )[0]
            boxes = res["boxes"].float().tolist()
            scores = res["scores"].float().tolist()
            rows = []
            refined_rows = []
            for i in detectors.dedupe(boxes, scores):
                circle = geometry.circle_from_box(*boxes[i])
                rows.append(
                    detectors._row(circle, scores[i], box_xyxy=[round(v, 2) for v in boxes[i]])
                )
                ref = rim_refine.refine_circle(bgr, circle)
                refined_rows.append(
                    detectors._row(ref.refined, scores[i], box_xyxy=[round(v, 2) for v in boxes[i]])
                )
            preds_by_id[int(img_entry["id"])] = rows
            refined_preds_by_id[int(img_entry["id"])] = refined_rows

        val_metrics = evaluate_box_predictions(preds_by_id, val_coco)
        val_refined_metrics = evaluate_box_predictions(refined_preds_by_id, val_coco)
        epoch_record = {
            "epoch": epoch,
            "train_loss_mean": round(total_loss / max(1, steps), 5),
            "learning_rate_end": optimizer.param_groups[0]["lr"],
            "augment_counts": dict(sorted(train_ds.augment_counts.items())),
            "elapsed_s": round(time.perf_counter() - started, 2),
            "val": val_metrics,
            "val_rim_refined": val_refined_metrics,
        }
        history.append(epoch_record)
        cur_score = float(val_metrics.get("frame_plausible_top1_rate") or 0.0)
        if cur_score > best_recall:
            best_recall = cur_score
            best_epoch = epoch
            best_checkpoint_dir.mkdir(parents=True, exist_ok=True)
            model.save_pretrained(best_checkpoint_dir)
            processor.save_pretrained(best_checkpoint_dir)
        print(
            f"epoch {epoch}/{epochs}: loss {epoch_record['train_loss_mean']} "
            f"recall {val_metrics['frame_recall']}/{val_metrics['evaluated_frames']} "
            f"plausible_top1 {val_metrics['frame_plausible_top1']} "
            f"refused {val_metrics['refusal']['refused']} "
            f"radius_err_med {val_metrics['radius_abs_error_rel_median']} "
            f"{epoch_record['elapsed_s']} s{' (best)' if best_epoch == epoch else ''}",
            flush=True,
        )

    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=HERE, capture_output=True, text=True, check=False
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--", str(HERE)], cwd=HERE, capture_output=True, text=True, check=False
    ).stdout.strip()
    checkpoint_hashes = {
        p.name: sha256_file(p)
        for p in sorted(best_checkpoint_dir.glob("*"))
        if p.is_file()
    }
    record = {
        "harness": HARNESS_ID,
        "origin": "machine",
        "code_commit": commit,
        "dirty": bool(dirty),
        "source_sha256": {p.name: sha256_file(p) for p in sorted(HERE.glob("*.py"))},
        "python": platform.python_version(),
        "student_architecture": {
            "base_checkpoint": base_checkpoint,
            "model_class": "DFineForObjectDetection",
            "license": "Apache-2.0",
            "contains_sam3_weights": False,
        },
        "training_config": {
            "epochs": epochs,
            "batch_size": batch_size,
            "learning_rate": learning_rate,
            "lr_schedule": lr_schedule,
            "seed": seed,
            "augment": augment,
            "augment_config": augment_config,
            "best_checkpoint_metric": "val.frame_plausible_top1_rate (first epoch wins ties)",
        },
        "dataset": validated["hashes"],
        "eval_dataset": eval_hashes,
        "best_epoch": best_epoch,
        "best_checkpoint_files_sha256": checkpoint_hashes,
        "epochs": history,
    }
    (output_dir / "student_run.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return record


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dataset-dir", type=Path, required=True, help="curated Phase B dataset directory")
    p.add_argument("--repo-root", type=Path, required=True, help="checkout holding validation/private manifests")
    p.add_argument("--output-dir", type=Path, required=True, help="new output directory under validation/private")
    p.add_argument("--base-checkpoint", default=detectors.DFINE_CHECKPOINT)
    p.add_argument("--epochs", type=int, default=10)
    p.add_argument("--batch-size", type=int, default=4)
    p.add_argument("--learning-rate", type=float, default=1e-4)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--manifest", type=Path, action="append")
    p.add_argument("--augment", choices=sorted(AUGMENT_CONFIGS), default=AUGMENT_NONE,
                   help="train-fold augmentation; student-aug-v1 = crop/flip/colour/motion blur/JPEG")
    p.add_argument("--lr-schedule", choices=LR_SCHEDULES, default="constant")
    p.add_argument("--eval-dataset-dir", type=Path, default=None,
                   help="take the val fold from this curated dataset (sessions must be disjoint from train)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    record = train_dfine_student(
        dataset_dir=args.dataset_dir,
        repo_root=args.repo_root,
        output_dir=args.output_dir,
        base_checkpoint=args.base_checkpoint,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        seed=args.seed,
        manifests=args.manifest,
        augment=args.augment,
        lr_schedule=args.lr_schedule,
        eval_dataset_dir=args.eval_dataset_dir,
    )
    print(json.dumps(record["epochs"][-1] if record["epochs"] else {}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
