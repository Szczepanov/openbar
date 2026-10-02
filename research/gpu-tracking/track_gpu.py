#!/usr/bin/env python3
"""GPU tracker runner for #57 Phase 3 bake-off.

Implements candidates 3–10:
  3. sam2.1-small-centroid
  4. sam2.1-small-circle
  5. sam2.1-bplus-centroid
  6. sam2.1-bplus-circle
  7. cutie-base-centroid
  8. cutie-base-circle
  9. bootstapir-affine
  10. cotracker3-affine

Decodes the seed-to-end frame window into temporary validation/private/work/gpu-tracking/<fixture>-<candidate>/
JPEGs and deletes them in a finally block. A mask model can write its centroid and circle candidates from one
run (--sibling-output).
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import torch
import torch.nn.functional as F

ROOT = Path(__file__).resolve().parents[2]
GPU_DIR = Path(__file__).resolve().parent
OPENCV_DIR = ROOT / "research" / "opencv-tracking"
MODELS_DIR = ROOT / "validation" / "private" / "models"
WORK_BASE = ROOT / "validation" / "private" / "work" / "gpu-tracking"

sys.path.insert(0, str(ROOT / "validation" / "tools"))
sys.path.insert(0, str(OPENCV_DIR))
sys.path.insert(0, str(GPU_DIR))

import centres  # noqa: E402
import download_models  # noqa: E402
import label_package  # noqa: E402
import point_motion  # noqa: E402

CANDIDATES = [
    "sam2.1-small-centroid",
    "sam2.1-small-circle",
    "sam2.1-bplus-centroid",
    "sam2.1-bplus-circle",
    "cutie-base-centroid",
    "cutie-base-circle",
    "bootstapir-affine",
    "cotracker3-affine",
]

SEED_TOLERANCE_S = 0.0005


class GpuTrackerError(RuntimeError):
    pass


MODEL_KEY_BY_CANDIDATE = {
    "sam2.1-small-centroid": "sam2.1_small",
    "sam2.1-small-circle": "sam2.1_small",
    "sam2.1-bplus-centroid": "sam2.1_bplus",
    "sam2.1-bplus-circle": "sam2.1_bplus",
    "cutie-base-centroid": "cutie_base",
    "cutie-base-circle": "cutie_base",
    "bootstapir-affine": "bootstapir",
    "cotracker3-affine": "cotracker3",
}
TRACKER_IMPORT_BY_CANDIDATE = {
    "sam2.1-small-centroid": "sam2",
    "sam2.1-small-circle": "sam2",
    "sam2.1-bplus-centroid": "sam2",
    "sam2.1-bplus-circle": "sam2",
    "cutie-base-centroid": "cutie",
    "cutie-base-circle": "cutie",
    "bootstapir-affine": "tapnet",
    "cotracker3-affine": "cotracker",
}


def require_verified_model(model_key: str) -> Path:
    """Return a checkpoint only after verifying it against the committed SHA-256."""
    spec = download_models.MODEL_SPECS[model_key]
    path = MODELS_DIR / spec["filename"]
    if not path.is_file():
        raise GpuTrackerError(f"checkpoint {spec['filename']} not found in {MODELS_DIR}")
    actual = download_models.compute_sha256(path)
    expected = spec["sha256"].lower()
    if actual != expected:
        raise GpuTrackerError(
            f"checkpoint {spec['filename']} SHA-256 mismatch: expected {expected}, got {actual}"
        )
    return path


def _distribution_provenance(distribution_name: str) -> dict[str, Any]:
    try:
        dist = importlib.metadata.distribution(distribution_name)
    except importlib.metadata.PackageNotFoundError:
        return {"distribution": distribution_name, "installed": False}

    result: dict[str, Any] = {
        "distribution": dist.metadata.get("Name", distribution_name),
        "version": dist.version,
    }
    direct_text = dist.read_text("direct_url.json")
    if direct_text:
        try:
            direct = json.loads(direct_text)
        except json.JSONDecodeError:
            direct = {}
        source: dict[str, Any] = {}
        url = direct.get("url")
        if isinstance(url, str) and not url.startswith("file:"):
            source["url"] = url
        vcs_info = direct.get("vcs_info")
        if isinstance(vcs_info, dict):
            source["vcs_info"] = {
                key: vcs_info[key]
                for key in ("vcs", "commit_id", "requested_revision")
                if key in vcs_info
            }
        dir_info = direct.get("dir_info")
        if isinstance(dir_info, dict) and dir_info.get("editable"):
            source["editable_local_install"] = True
        if source:
            result["direct_source"] = source
    return result


def _module_provenance(import_name: str) -> dict[str, Any]:
    names = sorted(set(importlib.metadata.packages_distributions().get(import_name, [])))
    return {
        "import_name": import_name,
        "distributions": [_distribution_provenance(name) for name in names],
    }


def runtime_dependency_provenance(candidate_name: str) -> dict[str, Any]:
    """Capture critical installed packages and any pip VCS direct-source metadata."""
    return {
        "python": platform.python_version(),
        "packages": [
            _distribution_provenance(name)
            for name in ("torch", "torchvision", "numpy", "opencv-python")
        ],
        "tracker_module": _module_provenance(TRACKER_IMPORT_BY_CANDIDATE[candidate_name]),
    }


def model_provenance(candidate_name: str) -> dict[str, str]:
    spec = download_models.MODEL_SPECS[MODEL_KEY_BY_CANDIDATE[candidate_name]]
    return {
        "filename": spec["filename"],
        "source_url": spec["url"],
        "sha256": spec["sha256"],
    }


def get_driver_version() -> str:
    try:
        out = subprocess.check_output(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"], text=True)
        return out.strip().splitlines()[0]
    except Exception:
        return "unknown"


def decode_to_jpegs(media: Path, work_dir: Path, first_index: int, last_index: int) -> list[Path]:
    """Decode the inclusive decoded-frame window [first_index, last_index] to JPEGs using ffmpeg -q:v 2.

    The whole stream is still decoded, so frame numbering matches the probe; only the window is written.
    The returned list is window-local: element 0 is decoded frame `first_index`.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    pattern = work_dir / "%06d.jpg"
    cmd = [
        "ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-autorotate",
        "-i", str(media),
        "-map", "0:v:0", "-vf", f"select='between(n\\,{first_index}\\,{last_index})'",
        "-fps_mode", "passthrough", "-enc_time_base", "demux",
        "-q:v", "2",
        str(pattern),
    ]
    ret = subprocess.run(cmd)
    if ret.returncode != 0:
        raise GpuTrackerError(f"ffmpeg exited with code {ret.returncode}")
    frames = sorted(work_dir.glob("*.jpg"))
    expected = last_index - first_index + 1
    if len(frames) != expected:
        raise GpuTrackerError(f"ffmpeg produced {len(frames)} JPEGs, expected {expected} for the tracked window")
    return frames


def load_inputs(manifest_path: Path, fixture_id: str, seed_path: Path, allow_held_out: bool) -> tuple[dict, Path, dict]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    fixture = next((f for f in manifest["fixtures"] if f["id"] == fixture_id), None)
    if fixture is None:
        raise GpuTrackerError(f"fixture {fixture_id!r} is not in {manifest_path}")
    if fixture["purpose"] == "validation" and not allow_held_out:
        raise GpuTrackerError(f"{fixture_id} is a held-out validation fixture; tune on development fixtures only")
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    if seed.get("fixture_id") not in (None, fixture_id):
        raise GpuTrackerError("seed fixture_id does not match")
    media = ROOT / fixture["media"]["repository_path"]
    label_package.require_media_hash(fixture, media)
    return fixture, media, seed


MaskFrames = dict[int, tuple[np.ndarray, float]]


def track_sam2(
    model_size: str,
    method: str,
    jpeg_paths: list[Path],
    seed_idx: int,
    end_idx: int,
    cx: float,
    cy: float,
    r_seed: float,
    width: int,
    height: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    masks, seed_area = sam2_masks(model_size, jpeg_paths, seed_idx, end_idx, cx, cy, r_seed, width, height)
    return masks_to_samples(method, masks, seed_area, seed_idx, end_idx, cx, cy, r_seed, width, height)


def sam2_masks(
    model_size: str,
    jpeg_paths: list[Path],
    seed_idx: int,
    end_idx: int,
    cx: float,
    cy: float,
    r_seed: float,
    width: int,
    height: int,
) -> tuple[MaskFrames, int]:
    """Propagate a SAM 2.1 mask forward from the seed. Every JPEG in the frames directory is loaded."""
    from sam2.build_sam import build_sam2_video_predictor

    cfg_name = f"configs/sam2.1/sam2.1_hiera_{'s' if model_size == 'small' else 'b+'}.yaml"
    model_key = "sam2.1_small" if model_size == "small" else "sam2.1_bplus"
    ckpt_path = require_verified_model(model_key)

    predictor = build_sam2_video_predictor(cfg_name, str(ckpt_path), device="cuda")
    frames_dir = jpeg_paths[0].parent

    with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
        # Frames stay in host RAM: a full-resolution clip as 1024² float tensors exceeds 8 GB of VRAM,
        # and the Windows driver then silently spills to shared memory. Numerics are unchanged.
        state = predictor.init_state(video_path=str(frames_dir), offload_video_to_cpu=True)
        box = np.array([
            max(0.0, cx - r_seed),
            max(0.0, cy - r_seed),
            min(float(width), cx + r_seed),
            min(float(height), cy + r_seed),
        ], dtype=np.float32)
        points = np.array([[cx, cy]], dtype=np.float32)
        labels = np.array([1], dtype=np.int32)

        _, _, out_mask_logits = predictor.add_new_points_or_box(
            state,
            frame_idx=seed_idx,
            obj_id=1,
            points=points,
            labels=labels,
            box=box,
        )

        seed_mask = (out_mask_logits[0, 0] > 0.0).cpu().numpy().astype(np.uint8)
        seed_area = int(np.count_nonzero(seed_mask))
        if seed_area == 0:
            seed_area = int(math.pi * (r_seed ** 2))

        # Propagate through video
        pred_dict: dict[int, tuple[np.ndarray, float]] = {}
        for f_idx, obj_ids, video_res_masks in predictor.propagate_in_video(
            state,
            start_frame_idx=seed_idx,
            max_frame_num_to_track=(end_idx - seed_idx + 1),
        ):
            if f_idx > end_idx:
                break
            mask = (video_res_masks[0, 0] > 0.0).cpu().numpy().astype(np.uint8)
            # Retrieve object score logit from state
            obj_dict = state["output_dict_per_obj"][0]
            if f_idx in obj_dict.get("cond_frame_outputs", {}):
                cur_out = obj_dict["cond_frame_outputs"][f_idx]
            else:
                cur_out = obj_dict["non_cond_frame_outputs"].get(f_idx, {})
            score_logit = cur_out.get("object_score_logits")
            if score_logit is not None:
                conf = float(torch.sigmoid(score_logit).item())
            else:
                conf = float(torch.sigmoid(video_res_masks[0, 0][mask > 0]).mean().item()) if np.any(mask) else 0.0
            pred_dict[f_idx] = (mask, min(1.0, max(0.0, conf)))

    return pred_dict, seed_area


def _seed_sample(f_idx: int, cx: float, cy: float) -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        {
            "frame_index": f_idx,
            "state": "tracked",
            "center_px": {"x_px": round(cx, 3), "y_px": round(cy, 3)},
            "confidence": 1.0,
        },
        {
            "frame_index": f_idx,
            "fit_attempted": False,
            "accepted": True,
            "reject_reasons": [],
            "base_confidence": 1.0,
        },
    )


def masks_to_samples(
    method: str,
    masks: MaskFrames,
    seed_area: int,
    seed_idx: int,
    end_idx: int,
    cx: float,
    cy: float,
    r_seed: float,
    width: int,
    height: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Turn per-frame masks into prediction and geometry-sidecar samples (§5.3).

    `centroid` and `circle` read the same masks, so one model run serves both candidates.
    """
    samples: list[dict[str, Any]] = []
    sidecar_samples: list[dict[str, Any]] = []

    for f_idx in range(seed_idx, end_idx + 1):
        if f_idx == seed_idx:
            sample, sidecar = _seed_sample(f_idx, cx, cy)
            samples.append(sample)
            sidecar_samples.append(sidecar)
            continue

        if f_idx not in masks:
            samples.append({"frame_index": f_idx, "state": "lost"})
            sidecar_samples.append({
                "frame_index": f_idx,
                "fit_attempted": False,
                "accepted": False,
                "reject_reasons": ["frame_missing"],
                "base_confidence": 0.0,
            })
            continue

        mask, base_conf = masks[f_idx]
        if not centres.validate_mask(mask, seed_area):
            samples.append({"frame_index": f_idx, "state": "lost"})
            sidecar_samples.append({
                "frame_index": f_idx,
                "fit_attempted": False,
                "accepted": False,
                "reject_reasons": ["mask_invalid_area"],
                "base_confidence": 0.0,
            })
            continue

        if method == "centroid":
            c = centres.extract_mask_centroid(mask, width, height)
            if c is not None:
                samples.append({
                    "frame_index": f_idx,
                    "state": "tracked",
                    "center_px": {"x_px": round(c[0], 3), "y_px": round(c[1], 3)},
                    "confidence": round(base_conf, 4),
                })
            else:
                samples.append({"frame_index": f_idx, "state": "lost"})
        elif method == "circle":
            c_res = centres.fit_circle_from_mask(mask, r_seed, f_idx - seed_idx, width, height)
            if c_res.accepted and c_res.center_px is not None:
                samples.append({
                    "frame_index": f_idx,
                    "state": "tracked",
                    "center_px": {"x_px": round(c_res.center_px[0], 3), "y_px": round(c_res.center_px[1], 3)},
                    "confidence": round(base_conf, 4),
                })
                sidecar_samples.append({
                    "frame_index": f_idx,
                    "fit_attempted": True,
                    "accepted": True,
                    "reject_reasons": [],
                    "radius_px": round(c_res.radius_px, 3) if c_res.radius_px else None,
                    "inlier_count": c_res.inlier_count,
                    "coverage_bins": c_res.coverage_bins,
                    "base_confidence": round(base_conf, 4),
                })
            elif c_res.center_px is not None:
                samples.append({
                    "frame_index": f_idx,
                    "state": "tracked",
                    "center_px": {"x_px": round(c_res.center_px[0], 3), "y_px": round(c_res.center_px[1], 3)},
                    "confidence": round(base_conf * 0.7, 4),
                })
                sidecar_samples.append({
                    "frame_index": f_idx,
                    "fit_attempted": True,
                    "accepted": False,
                    "reject_reasons": c_res.reject_reasons,
                    "radius_px": round(c_res.radius_px, 3) if c_res.radius_px else None,
                    "inlier_count": c_res.inlier_count,
                    "coverage_bins": c_res.coverage_bins,
                    "base_confidence": round(base_conf, 4),
                })
            else:
                samples.append({"frame_index": f_idx, "state": "lost"})
                sidecar_samples.append({
                    "frame_index": f_idx,
                    "fit_attempted": True,
                    "accepted": False,
                    "reject_reasons": c_res.reject_reasons,
                    "base_confidence": 0.0,
                })
        else:
            raise GpuTrackerError(f"unknown centre method {method!r}")

    return samples, sidecar_samples


def track_cutie(
    method: str,
    jpeg_paths: list[Path],
    seed_idx: int,
    end_idx: int,
    cx: float,
    cy: float,
    r_seed: float,
    width: int,
    height: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    masks, seed_area = cutie_masks(jpeg_paths, seed_idx, end_idx, cx, cy, r_seed, width, height)
    return masks_to_samples(method, masks, seed_area, seed_idx, end_idx, cx, cy, r_seed, width, height)


def cutie_masks(
    jpeg_paths: list[Path],
    seed_idx: int,
    end_idx: int,
    cx: float,
    cy: float,
    r_seed: float,
    width: int,
    height: int,
) -> tuple[MaskFrames, int]:
    """Propagate a Cutie mask forward from a rasterised seed disk."""
    import hydra
    from cutie.inference.inference_core import InferenceCore
    from cutie.inference.utils.args_utils import get_dataset_cfg
    from cutie.model.cutie import CUTIE
    from omegaconf import open_dict

    ckpt_path = require_verified_model("cutie_base")

    with hydra.initialize_config_module("cutie.config", version_base="1.3.2"):
        cfg = hydra.compose(config_name="eval_config")
    with open_dict(cfg):
        cfg["weights"] = str(ckpt_path)
    get_dataset_cfg(cfg)

    cutie = CUTIE(cfg).cuda().eval()
    cutie.load_weights(torch.load(cfg.weights))
    processor = InferenceCore(cutie, cfg=cfg)

    seed_disk = centres.rasterize_seed_disk(cx, cy, r_seed, width, height)
    seed_area = int(np.count_nonzero(seed_disk))

    masks: MaskFrames = {}
    with torch.inference_mode():
        for f_idx in range(seed_idx, end_idx + 1):
            img_bgr = cv2.imread(str(jpeg_paths[f_idx]))
            img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
            img_tensor = torch.from_numpy(img_rgb).permute(2, 0, 1).float().cuda() / 255.0

            if f_idx == seed_idx:
                mask_tensor = torch.from_numpy(seed_disk).cuda()
                processor.step(img_tensor, mask=mask_tensor, objects=[1], force_permanent=True)
                continue

            output_prob = processor.step(img_tensor)  # (2, H, W)
            pred_mask = (output_prob.argmax(dim=0) == 1).cpu().numpy().astype(np.uint8)
            fg_prob = output_prob[1]
            if np.any(pred_mask):
                base_conf = float(fg_prob[torch.from_numpy(pred_mask).cuda() > 0].mean().item())
            else:
                base_conf = 0.0
            masks[f_idx] = (pred_mask, base_conf)

    return masks, seed_area


def track_cotracker(
    jpeg_paths: list[Path],
    seed_idx: int,
    end_idx: int,
    cx: float,
    cy: float,
    r_seed: float,
    width: int,
    height: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from cotracker.predictor import CoTrackerPredictor

    ckpt_path = require_verified_model("cotracker3")

    model = CoTrackerPredictor(checkpoint=str(ckpt_path)).to("cuda")

    seed_bgr = cv2.imread(str(jpeg_paths[seed_idx]))
    seed_gray = cv2.cvtColor(seed_bgr, cv2.COLOR_BGR2GRAY)
    seed_pts = point_motion.extract_query_points(seed_gray, cx, cy, r_seed)
    initial_query_count = len(seed_pts)
    if initial_query_count == 0:
        raise GpuTrackerError("no query points found on seed frame")

    # Load video slice into memory
    num_frames = end_idx - seed_idx + 1
    frames_np = np.zeros((num_frames, height, width, 3), dtype=np.uint8)
    for i, f_idx in enumerate(range(seed_idx, end_idx + 1)):
        frames_np[i] = cv2.cvtColor(cv2.imread(str(jpeg_paths[f_idx])), cv2.COLOR_BGR2RGB)

    video_tensor = torch.from_numpy(frames_np).permute(0, 3, 1, 2).unsqueeze(0).float().cuda()  # (1, T, 3, H, W)
    queries = np.zeros((1, initial_query_count, 3), dtype=np.float32)
    queries[0, :, 0] = 0  # query frame index relative to video slice
    queries[0, :, 1] = seed_pts[:, 0]  # x
    queries[0, :, 2] = seed_pts[:, 1]  # y
    queries_tensor = torch.from_numpy(queries).cuda()

    with torch.inference_mode():
        pred_tracks, pred_visibility = model(video_tensor, queries=queries_tensor)

    tracks = pred_tracks[0].cpu().numpy()  # (T, N, 2)
    visibility = pred_visibility[0].cpu().numpy()  # (T, N)

    samples: list[dict[str, Any]] = []
    sidecar_samples: list[dict[str, Any]] = []

    for t_idx in range(num_frames):
        f_idx = seed_idx + t_idx
        if t_idx == 0:
            samples.append({
                "frame_index": f_idx,
                "state": "tracked",
                "center_px": {"x_px": round(cx, 3), "y_px": round(cy, 3)},
                "confidence": 1.0,
            })
            sidecar_samples.append({
                "frame_index": f_idx,
                "fit_attempted": False,
                "accepted": True,
                "reject_reasons": [],
                "base_confidence": 1.0,
            })
            continue

        vis = visibility[t_idx] > 0.5
        cur_pts = tracks[t_idx]

        res = point_motion.estimate_center_from_points(
            seed_pts[vis],
            cur_pts[vis],
            (cx, cy),
            initial_query_count,
            width,
            height,
        )
        if res.state == "tracked" and res.center_px is not None:
            samples.append({
                "frame_index": f_idx,
                "state": "tracked",
                "center_px": {"x_px": round(res.center_px[0], 3), "y_px": round(res.center_px[1], 3)},
                "confidence": round(res.confidence, 4),
            })
        else:
            samples.append({"frame_index": f_idx, "state": "lost"})

    return samples, sidecar_samples


def track_bootstapir(
    jpeg_paths: list[Path],
    seed_idx: int,
    end_idx: int,
    cx: float,
    cy: float,
    r_seed: float,
    width: int,
    height: int,
    target_res: int = 512,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    from tapnet.torch import tapir_model

    ckpt_path = require_verified_model("bootstapir")

    model = tapir_model.TAPIR(pyramid_level=1, use_casual_conv=False)
    model.load_state_dict(torch.load(str(ckpt_path)))
    model = model.to("cuda").eval()

    seed_bgr = cv2.imread(str(jpeg_paths[seed_idx]))
    seed_gray = cv2.cvtColor(seed_bgr, cv2.COLOR_BGR2GRAY)
    seed_pts = point_motion.extract_query_points(seed_gray, cx, cy, r_seed)
    initial_query_count = len(seed_pts)
    if initial_query_count == 0:
        raise GpuTrackerError("no query points found on seed frame")

    # Load video slice into memory and resize to target_res x target_res
    num_frames = end_idx - seed_idx + 1

    def run_inference_at_res(res: int) -> tuple[np.ndarray, np.ndarray]:
        frames_np = np.zeros((1, num_frames, res, res, 3), dtype=np.float32)
        for i, f_idx in enumerate(range(seed_idx, end_idx + 1)):
            img_bgr = cv2.imread(str(jpeg_paths[f_idx]))
            img_resized = cv2.resize(img_bgr, (res, res), interpolation=cv2.INTER_AREA)
            img_rgb = cv2.cvtColor(img_resized, cv2.COLOR_BGR2RGB)
            frames_np[0, i] = (img_rgb.astype(np.float32) / 255.0) * 2.0 - 1.0

        video_t = torch.from_numpy(frames_np).cuda()

        # Query points in model coordinates: [t, y, x]
        queries_np = np.zeros((1, initial_query_count, 3), dtype=np.float32)
        queries_np[0, :, 0] = 0.0
        for p_idx in range(initial_query_count):
            xm, ym = centres.convert_frame_to_model(seed_pts[p_idx, 0], seed_pts[p_idx, 1], width, height, res, res)
            queries_np[0, p_idx, 1] = ym
            queries_np[0, p_idx, 2] = xm
        queries_t = torch.from_numpy(queries_np).cuda()

        with torch.inference_mode():
            outputs = model(video_t, queries_t)
            tracks_m = outputs["tracks"][0].cpu().numpy()  # (N, T, 2) in [x, y]
            occ = outputs["occlusion"][0]  # (N, T)
            dist = outputs["expected_dist"][0]  # (N, T)
            vis = ((1.0 - torch.sigmoid(occ)) * (1.0 - torch.sigmoid(dist)) > 0.5).cpu().numpy()

        return tracks_m, vis

    actual_res = target_res
    try:
        tracks_model, vis_matrix = run_inference_at_res(target_res)
    except torch.cuda.OutOfMemoryError:
        torch.cuda.empty_cache()
        actual_res = 256
        tracks_model, vis_matrix = run_inference_at_res(256)

    samples: list[dict[str, Any]] = []
    sidecar_samples: list[dict[str, Any]] = []

    for t_idx in range(num_frames):
        f_idx = seed_idx + t_idx
        if t_idx == 0:
            samples.append({
                "frame_index": f_idx,
                "state": "tracked",
                "center_px": {"x_px": round(cx, 3), "y_px": round(cy, 3)},
                "confidence": 1.0,
            })
            sidecar_samples.append({
                "frame_index": f_idx,
                "fit_attempted": False,
                "accepted": True,
                "reject_reasons": [],
                "base_confidence": 1.0,
            })
            continue

        vis = vis_matrix[:, t_idx]
        cur_pts_frame = np.zeros((initial_query_count, 2), dtype=np.float64)
        for p_idx in range(initial_query_count):
            xm, ym = tracks_model[p_idx, t_idx, 0], tracks_model[p_idx, t_idx, 1]
            xf, yf = centres.convert_model_to_frame(xm, ym, width, height, actual_res, actual_res)
            cur_pts_frame[p_idx] = (xf, yf)

        res = point_motion.estimate_center_from_points(
            seed_pts[vis],
            cur_pts_frame[vis],
            (cx, cy),
            initial_query_count,
            width,
            height,
        )
        if res.state == "tracked" and res.center_px is not None:
            samples.append({
                "frame_index": f_idx,
                "state": "tracked",
                "center_px": {"x_px": round(res.center_px[0], 3), "y_px": round(res.center_px[1], 3)},
                "confidence": round(res.confidence, 4),
            })
        else:
            samples.append({"frame_index": f_idx, "state": "lost"})

    return samples, sidecar_samples, actual_res


def sibling_candidate(candidate_name: str) -> str:
    """Return the other centre method of the same mask model (centroid <-> circle)."""
    if candidate_name.endswith("-centroid"):
        return candidate_name.removesuffix("-centroid") + "-circle"
    if candidate_name.endswith("-circle"):
        return candidate_name.removesuffix("-circle") + "-centroid"
    raise GpuTrackerError(f"{candidate_name} is not a mask candidate and has no sibling")


def candidate_config(candidate_name: str) -> dict[str, Any]:
    method = "circle" if candidate_name.endswith("-circle") else (
        "centroid" if candidate_name.endswith("-centroid") else "affine"
    )
    config: dict[str, Any] = {
        "model": model_provenance(candidate_name),
        "dependencies": runtime_dependency_provenance(candidate_name),
        "center_method": method,
    }

    if candidate_name.startswith("sam2"):
        config.update({
            "license": "Apache-2.0 / Apache-2.0",
            "family": "video_segmentation",
            "initialization": "seed_circle_box_plus_positive_center_point",
            "mask_logits_threshold": 0.0,
            "mask_area_ratio_bounds": [0.5, 1.5],
            "confidence": "sigmoid_object_score_logit",
            "autocast": "bfloat16",
            "offload_video_to_cpu": True,
        })
    elif candidate_name.startswith("cutie"):
        config.update({
            "license": "MIT / unconfirmed weights",
            "family": "video_segmentation",
            "initialization": "rasterized_seed_disk",
            "mask": "foreground_argmax",
            "mask_area_ratio_bounds": [0.5, 1.5],
            "confidence": "mean_foreground_probability_over_mask",
        })
    elif candidate_name == "bootstapir-affine":
        config.update({
            "license": "Apache-2.0 / Apache-2.0",
            "family": "neural_point_tracking",
            "query_annulus_fraction": [0.25, 0.90],
            "query_max_corners": 200,
            "visibility": "(1-sigmoid(occlusion))*(1-sigmoid(expected_dist)) > 0.5",
            "target_model_resolution": [512, 512],
            "oom_fallback_resolution": [256, 256],
            "confidence": "ransac_inliers_fraction_of_initial_queries",
        })
    elif candidate_name == "cotracker3-affine":
        config.update({
            "license": "CC-BY-NC-4.0 / CC-BY-NC-4.0",
            "family": "neural_point_tracking",
            "query_annulus_fraction": [0.25, 0.90],
            "query_max_corners": 200,
            "visibility_threshold": 0.5,
            "confidence": "ransac_inliers_fraction_of_initial_queries",
        })
    else:
        raise GpuTrackerError(f"unhandled candidate {candidate_name}")

    if method == "circle":
        config["circle_fit"] = {
            "ransac_iterations": 1000,
            "inlier_tolerance_px": 1.5,
            "coverage_bins_min": 18,
            "coverage_bins_total": 36,
            "seed_radius_relative_tolerance": 0.20,
            "rejected_fit_confidence_multiplier": 0.7,
        }
    if method == "affine":
        config["center_estimator"] = {
            "ransac_reprojection_threshold_px": 1.5,
            "ransac_max_iterations": 2000,
            "ransac_confidence": 0.999,
            "ransac_refine_iterations": 10,
            "minimum_inliers": 12,
        }
    return config


def run_candidates(
    candidate_names: list[str],
    jpeg_paths: list[Path],
    cx: float,
    cy: float,
    r_seed: float,
    width: int,
    height: int,
) -> tuple[dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]], int | None]:
    """Run one model over the window-local frames (seed is index 0) for one or two candidates.

    Two candidates are only allowed as the centroid/circle pair of one mask model: the model runs once and
    both centre methods read the same masks.
    """
    seed_idx, end_idx = 0, len(jpeg_paths) - 1
    primary = candidate_names[0]
    if len(candidate_names) == 2 and candidate_names[1] != sibling_candidate(primary):
        raise GpuTrackerError(f"{candidate_names[1]} is not the sibling of {primary}")

    if primary.startswith(("sam2.1-", "cutie-base-")):
        if primary.startswith("sam2.1-small"):
            masks, seed_area = sam2_masks("small", jpeg_paths, seed_idx, end_idx, cx, cy, r_seed, width, height)
        elif primary.startswith("sam2.1-bplus"):
            masks, seed_area = sam2_masks("base_plus", jpeg_paths, seed_idx, end_idx, cx, cy, r_seed, width, height)
        else:
            masks, seed_area = cutie_masks(jpeg_paths, seed_idx, end_idx, cx, cy, r_seed, width, height)
        results = {}
        for name in candidate_names:
            method = "circle" if name.endswith("-circle") else "centroid"
            results[name] = masks_to_samples(
                method, masks, seed_area, seed_idx, end_idx, cx, cy, r_seed, width, height
            )
        return results, None

    if len(candidate_names) != 1:
        raise GpuTrackerError(f"{primary} cannot share a run with another candidate")
    if primary == "cotracker3-affine":
        return {primary: track_cotracker(jpeg_paths, seed_idx, end_idx, cx, cy, r_seed, width, height)}, None
    if primary == "bootstapir-affine":
        raw, sidecar, res = track_bootstapir(jpeg_paths, seed_idx, end_idx, cx, cy, r_seed, width, height)
        return {primary: (raw, sidecar)}, res
    raise GpuTrackerError(f"unhandled candidate {primary}")


def track(
    manifest_path: Path,
    fixture_id: str,
    seed_path: Path,
    candidate_names: list[str],
    end_s: float | None,
    allow_held_out: bool,
) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
    """Track one fixture and return (prediction, geometry sidecar) per requested candidate, in order."""
    for name in candidate_names:
        if name not in CANDIDATES:
            raise GpuTrackerError(f"unknown candidate {name!r}; choices: {CANDIDATES}")

    started = time.perf_counter()
    fixture, media, seed_doc = load_inputs(manifest_path, fixture_id, seed_path, allow_held_out)
    probed = label_package.probe(media)
    label_package.require_fixture_probe_match(fixture, probed)
    width, height = label_package.display_size(probed["width_px"], probed["height_px"], probed["rotation_deg"])
    timestamps = probed["timestamps_s"]

    seed = seed_doc["seed"]
    seed_timestamp_s = seed["timestamp_s"]
    center, radius = seed["target"]["center"], seed["target"]["radius_px"]
    cx, cy, r_seed = float(center["x_px"]), float(center["y_px"]), float(radius)

    seed_index = min(range(len(timestamps)), key=lambda i: abs(timestamps[i] - float(seed_timestamp_s)))
    if abs(timestamps[seed_index] - float(seed_timestamp_s)) > SEED_TOLERANCE_S:
        raise GpuTrackerError(f"seed timestamp {seed_timestamp_s} s matches no decoded frame")

    last_s = timestamps[-1] if end_s is None else end_s
    # Last decoded frame at or before last_s
    end_index = max(seed_index, max(i for i, t in enumerate(timestamps) if t <= last_s + SEED_TOLERANCE_S))

    work_dir = WORK_BASE / f"{fixture_id}-{candidate_names[0]}"
    # Determinism flags (§4)
    cv2.setNumThreads(1)
    torch.manual_seed(0)
    torch.backends.cudnn.benchmark = False
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()

    try:
        # Only the tracked window is written: trackers run forward from the seed and never read earlier frames.
        jpeg_paths = decode_to_jpegs(media, work_dir, seed_index, end_index)
        results, actual_bootstapir_res = run_candidates(candidate_names, jpeg_paths, cx, cy, r_seed, width, height)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    processing_wall_s = time.perf_counter() - started
    peak_mem_bytes = torch.cuda.max_memory_allocated() if torch.cuda.is_available() else 0
    peak_mem_mb = round(peak_mem_bytes / (1024 * 1024), 2)
    device_name = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "None"
    driver_version = get_driver_version()

    def timestamp_of(local_index: int) -> float:
        return round(timestamps[seed_index + local_index], 6)

    documents: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    for name in candidate_names:
        raw_samples, sidecar_samples = results[name]
        samples: list[dict[str, Any]] = []
        for s in raw_samples:
            sample_dict: dict[str, Any] = {"timestamp_s": timestamp_of(s["frame_index"]), "state": s["state"]}
            if s["state"] == "tracked":
                sample_dict["center_px"] = s["center_px"]
                sample_dict["confidence"] = s["confidence"]
            samples.append(sample_dict)

        out_sidecar_samples = [
            {**{k: v for k, v in s.items() if k != "frame_index"}, "timestamp_s": timestamp_of(s["frame_index"])}
            for s in sidecar_samples
        ]

        config: dict[str, Any] = {
            "candidate": name,
            "seed_timestamp_s": seed_timestamp_s,
            "end_s": last_s,
            "gpu_model": device_name,
            "driver_version": driver_version,
            "peak_gpu_memory_mb": peak_mem_mb,
            "lossy_frame_cache": "jpeg_q2",
            "frame_cache_window": "seed_to_end",
            **candidate_config(name),
        }
        if len(candidate_names) > 1:
            # One model run produced every listed candidate; runtime and peak memory are for that shared run.
            config["shared_model_run"] = list(candidate_names)
        if actual_bootstapir_res is not None:
            config["model_resolution"] = [actual_bootstapir_res, actual_bootstapir_res]

        implementation = {"name": name, "version": "gpu-spike-2", "config": config}
        prediction: dict[str, Any] = {
            "schema_version": 1,
            "fixture_id": fixture_id,
            "source_video_sha256": fixture["media"]["sha256"],
            "coordinate_space": "decoded_display_pixels",
            "implementation": implementation,
            "runtime": {"processing_wall_s": processing_wall_s},
            "samples": samples,
        }
        sidecar_doc: dict[str, Any] | None = None
        if name.endswith("-circle") and out_sidecar_samples:
            sidecar_doc = {
                "format": "openbar-research-geometry-sidecar",
                "format_version": 0,
                "fixture_id": fixture_id,
                "implementation": implementation,
                "samples": out_sidecar_samples,
            }
        documents.append((prediction, sidecar_doc))

    return documents


def write_documents(output: Path, prediction: dict[str, Any], sidecar_doc: dict[str, Any] | None) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(prediction, indent=2) + "\n", encoding="utf-8")
    if sidecar_doc is not None:
        stem = output.name
        if stem.endswith(".prediction-v1.json"):
            sidecar_name = stem.removesuffix(".prediction-v1.json") + ".geometry.json"
        elif stem.endswith(".json"):
            sidecar_name = stem.removesuffix(".json") + ".geometry.json"
        else:
            sidecar_name = stem + ".geometry.json"
        (output.parent / sidecar_name).write_text(json.dumps(sidecar_doc, indent=2) + "\n", encoding="utf-8")

    tracked = sum(s["state"] == "tracked" for s in prediction["samples"])
    print(f"{prediction['implementation']['name']}: {tracked}/{len(prediction['samples'])} tracked, "
          f"{prediction['runtime']['processing_wall_s']:.2f} s -> {output}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--candidate", choices=CANDIDATES, required=True)
    parser.add_argument("--end-s", type=float, help="last timestamp to track")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--sibling-output",
        type=Path,
        help="mask candidates only: also write the other centre method (centroid <-> circle) from the same run",
    )
    parser.add_argument("--allow-held-out", action="store_true")
    args = parser.parse_args(argv)

    try:
        names = [args.candidate]
        outputs = [args.output]
        if args.sibling_output is not None:
            names.append(sibling_candidate(args.candidate))
            outputs.append(args.sibling_output)
        documents = track(args.manifest, args.fixture, args.seed, names, args.end_s, args.allow_held_out)
    except (GpuTrackerError, label_package.PackageError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    for output, (prediction, sidecar_doc) in zip(outputs, documents):
        write_documents(output, prediction, sidecar_doc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
