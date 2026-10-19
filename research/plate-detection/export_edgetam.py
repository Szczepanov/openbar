#!/usr/bin/env python3
"""Export EdgeTAM video tracker components to static-shape mobile graphs and verify parity (#251, #57).

Exports the EdgeTAM (Apache-2.0, ``yonigozlan/EdgeTAM-hf``) vision encoder, memory encoder and mask decoder
subgraphs to static-shape PyTorch 2 ``ExportedProgram`` archives (``.pt2`` for ExecuTorch /
LiteRT-Torch / AOTInductor mobile deployment, and optionally ``.onnx`` when ``onnx`` /
``onnxscript`` is available), verifies numerical output parity against eager PyTorch on synthetic
tensors and validation frames, and records SHA-256 provenance under ``validation/private/``.

Pure configuration and parity verification helpers are NumPy/stdlib only and unit-tested without
loading model weights. Desktop latency timings recorded by this script are explicitly marked
``NON-QUALIFYING DIAGNOSTIC`` and never substitute for Xiaomi 14T Pro on-device measurements (#179).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Sequence

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "validation" / "tools"))

import label_package  # noqa: E402
import track  # noqa: E402
import train_phase_b  # noqa: E402

HARNESS_ID = "edgetam-mobile-export-v1"
DEFAULT_INPUT_HEIGHT_PX = 1024
DEFAULT_INPUT_WIDTH_PX = 1024
DEFAULT_MAX_ABS_DIFF = 1e-4

EDGETAM_CHECKPOINT = track.TRACKERS["edgetam"]["checkpoint"]
EDGETAM_REVISION = track.TRACKERS["edgetam"]["revision"]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _finite_float(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def validate_export_config(
    *,
    input_height_px: int = DEFAULT_INPUT_HEIGHT_PX,
    input_width_px: int = DEFAULT_INPUT_WIDTH_PX,
    max_abs_diff: float = DEFAULT_MAX_ABS_DIFF,
) -> dict[str, Any]:
    """Validate static input shapes and parity tolerances."""
    if (
        isinstance(input_height_px, bool)
        or not isinstance(input_height_px, int)
        or input_height_px < 32
        or input_height_px % 32 != 0
    ):
        raise ValueError("input_height_px must be a positive integer multiple of 32")
    if (
        isinstance(input_width_px, bool)
        or not isinstance(input_width_px, int)
        or input_width_px < 32
        or input_width_px % 32 != 0
    ):
        raise ValueError("input_width_px must be a positive integer multiple of 32")
    tol = _finite_float(max_abs_diff, "max_abs_diff")
    if tol <= 0.0:
        raise ValueError("max_abs_diff must be positive")
    return {
        "vision_input_shape": [1, 3, input_height_px, input_width_px],
        "memory_pix_shape": [1, 256, input_height_px // 16, input_width_px // 16],
        "memory_mask_shape": [1, 1, input_height_px, input_width_px],
        "decoder_emb_shape": [1, 256, input_height_px // 16, input_width_px // 16],
        "decoder_sparse_shape": [1, 1, 2, 256],
        "decoder_high_res_0_shape": [1, 256, input_height_px // 4, input_width_px // 4],
        "decoder_high_res_1_shape": [1, 256, input_height_px // 8, input_width_px // 8],
        "max_abs_diff": tol,
    }


def compute_feature_parity(
    ref_tensors: Sequence[np.ndarray],
    exp_tensors: Sequence[np.ndarray],
    *,
    max_abs_diff: float = DEFAULT_MAX_ABS_DIFF,
) -> dict[str, Any]:
    """Compare eager reference tensors against exported graph outputs."""
    if len(ref_tensors) != len(exp_tensors):
        raise ValueError(f"tensor count mismatch: {len(ref_tensors)} vs {len(exp_tensors)}")
    if not ref_tensors:
        raise ValueError("ref_tensors must not be empty")

    tol = _finite_float(max_abs_diff, "max_abs_diff")
    if tol <= 0.0:
        raise ValueError("max_abs_diff must be positive")

    per_tensor_diffs = []
    overall_max_diff = 0.0
    overall_mean_diff = 0.0

    for idx, (ref, exp) in enumerate(zip(ref_tensors, exp_tensors)):
        ref_arr = np.asarray(ref, dtype=np.float32)
        exp_arr = np.asarray(exp, dtype=np.float32)
        if ref_arr.shape != exp_arr.shape:
            raise ValueError(f"tensor {idx} shape mismatch: {ref_arr.shape} vs {exp_arr.shape}")
        if not np.all(np.isfinite(ref_arr)) or not np.all(np.isfinite(exp_arr)):
            raise ValueError(f"tensor {idx} contains non-finite values")

        abs_diff = np.abs(ref_arr - exp_arr)
        max_diff = float(np.max(abs_diff)) if abs_diff.size > 0 else 0.0
        mean_diff = float(np.mean(abs_diff)) if abs_diff.size > 0 else 0.0

        per_tensor_diffs.append({
            "tensor_index": idx,
            "shape": list(ref_arr.shape),
            "max_abs_diff": max_diff,
            "mean_abs_diff": mean_diff,
            "passed": bool(max_diff <= tol),
        })
        overall_max_diff = max(overall_max_diff, max_diff)
        overall_mean_diff += mean_diff

    overall_mean_diff /= len(ref_tensors)
    passed = all(t["passed"] for t in per_tensor_diffs)

    return {
        "passed": passed,
        "overall_max_abs_diff": overall_max_diff,
        "overall_mean_abs_diff": overall_mean_diff,
        "per_tensor": per_tensor_diffs,
    }


def git_commit_sha(repo_root: Path) -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
        return proc.stdout.strip()
    except Exception:
        return "unknown"


def _system_metadata() -> dict[str, Any]:
    gpu_name = None
    cuda_ver = None
    try:
        import torch
        if torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0)
            cuda_ver = torch.version.cuda
    except Exception:
        pass
    return {
        "hostname": platform.node(),
        "platform": platform.platform(),
        "python_version": sys.version,
        "gpu_name": gpu_name,
        "cuda_version": cuda_ver,
    }


def _measure_latency(fn: Any, repeats: int = 5) -> dict[str, Any]:
    """Measure wall-clock latency across repeats (marked NON-QUALIFYING DIAGNOSTIC)."""
    # Warmup
    for _ in range(2):
        fn()

    times_ms = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        times_ms.append((time.perf_counter() - t0) * 1000.0)

    return {
        "repeats": repeats,
        "median_ms": round(statistics.median(times_ms), 2),
        "mean_ms": round(statistics.mean(times_ms), 2),
        "min_ms": round(min(times_ms), 2),
        "max_ms": round(max(times_ms), 2),
        "diagnostic_disclaimer": "NON-QUALIFYING DIAGNOSTIC desktop timing; never substituted for phone evidence (#179)",
    }


def export_edgetam_tracker(
    *,
    output_dir: Path,
    repo_root: Path = ROOT,
    input_height_px: int = DEFAULT_INPUT_HEIGHT_PX,
    input_width_px: int = DEFAULT_INPUT_WIDTH_PX,
    max_abs_diff: float = DEFAULT_MAX_ABS_DIFF,
    export_onnx: bool = False,
    dataset_dir: Path | None = None,
) -> dict[str, Any]:
    """Export EdgeTAM subgraphs to static-shape .pt2 (and optionally .onnx) with parity verification."""
    output_dir = output_dir.resolve()
    private_root = label_package.PRIVATE_ROOT.resolve()
    if not output_dir.is_relative_to(private_root):
        raise SystemExit(f"output_dir must be under {label_package.PRIVATE_ROOT}; refusing {output_dir}")

    config = validate_export_config(
        input_height_px=input_height_px,
        input_width_px=input_width_px,
        max_abs_diff=max_abs_diff,
    )

    val_coco = None
    if dataset_dir is not None:
        validated = train_phase_b.validate_dataset_dir(dataset_dir, repo_root)
        val_coco = validated["val_coco"]

    output_dir.mkdir(parents=True, exist_ok=True)

    import torch
    import torch.nn as nn
    import transformers

    print(f"Loading EdgeTAM checkpoint {EDGETAM_CHECKPOINT} (revision {EDGETAM_REVISION})...")
    model = transformers.EdgeTamVideoModel.from_pretrained(
        EDGETAM_CHECKPOINT,
        revision=EDGETAM_REVISION,
    ).eval()

    # --- Subgraph 1: Vision Encoder Wrapper ---
    class EdgeTamVisionEncoderWrapper(nn.Module):
        def __init__(self, encoder: nn.Module) -> None:
            super().__init__()
            self.encoder = encoder

        def forward(self, pixel_values: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
            out = self.encoder(pixel_values)
            fpn = out.fpn_hidden_states
            return fpn[0], fpn[1], fpn[2]

    # --- Subgraph 2: Memory Encoder Wrapper ---
    class EdgeTamMemoryEncoderWrapper(nn.Module):
        def __init__(self, mem_encoder: nn.Module) -> None:
            super().__init__()
            self.mem_encoder = mem_encoder

        def forward(self, pix_feat: torch.Tensor, mask: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
            mem_features, mem_pos = self.mem_encoder(pix_feat, mask)
            return mem_features, mem_pos

    # --- Subgraph 3: Mask Decoder Wrapper ---
    class EdgeTamMaskDecoderWrapper(nn.Module):
        def __init__(self, mask_decoder: nn.Module) -> None:
            super().__init__()
            self.mask_decoder = mask_decoder

        def forward(
            self,
            image_embeddings: torch.Tensor,
            image_positional_embeddings: torch.Tensor,
            sparse_prompt_embeddings: torch.Tensor,
            dense_prompt_embeddings: torch.Tensor,
            high_res_0: torch.Tensor,
            high_res_1: torch.Tensor,
        ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
            masks, iou_pred, _, obj_score = self.mask_decoder(
                image_embeddings=image_embeddings,
                image_positional_embeddings=image_positional_embeddings,
                sparse_prompt_embeddings=sparse_prompt_embeddings,
                dense_prompt_embeddings=dense_prompt_embeddings,
                multimask_output=False,
                # Native EdgeTAM projects raw FPN features before calling the decoder.
                high_resolution_features=[self.mask_decoder.conv_s0(high_res_0), self.mask_decoder.conv_s1(high_res_1)],
            )
            return masks, iou_pred, obj_score

    vision_wrapper = EdgeTamVisionEncoderWrapper(model.vision_encoder)
    memory_wrapper = EdgeTamMemoryEncoderWrapper(model.memory_encoder)
    decoder_wrapper = EdgeTamMaskDecoderWrapper(model.mask_decoder)

    # 1. Export Vision Encoder
    print("Exporting EdgeTAM Vision Encoder to torch.export static-shape program...")
    dummy_vision = torch.randn(config["vision_input_shape"])
    ep_vision = torch.export.export(vision_wrapper, (dummy_vision,))
    vision_pt2_path = output_dir / "edgetam_vision_fp32.pt2"
    torch.export.save(ep_vision, str(vision_pt2_path))
    vision_pt2_sha256 = sha256_file(vision_pt2_path)
    vision_pt2_size = vision_pt2_path.stat().st_size
    print(f"Vision encoder saved to {vision_pt2_path} ({vision_pt2_size} bytes, SHA-256: {vision_pt2_sha256[:16]}...)")

    # 2. Export Memory Encoder
    print("Exporting EdgeTAM Memory Encoder to torch.export static-shape program...")
    dummy_pix = torch.randn(config["memory_pix_shape"])
    dummy_mask = torch.randn(config["memory_mask_shape"])
    ep_memory = torch.export.export(memory_wrapper, (dummy_pix, dummy_mask))
    memory_pt2_path = output_dir / "edgetam_memory_fp32.pt2"
    torch.export.save(ep_memory, str(memory_pt2_path))
    memory_pt2_sha256 = sha256_file(memory_pt2_path)
    memory_pt2_size = memory_pt2_path.stat().st_size
    print(f"Memory encoder saved to {memory_pt2_path} ({memory_pt2_size} bytes, SHA-256: {memory_pt2_sha256[:16]}...)")

    # 3. Export Mask Decoder
    print("Exporting EdgeTAM Mask Decoder to torch.export static-shape program...")
    dummy_dec_args = (
        torch.randn(config["decoder_emb_shape"]),
        torch.randn(config["decoder_emb_shape"]),
        torch.randn(config["decoder_sparse_shape"]),
        torch.randn(config["decoder_emb_shape"]),
        torch.randn(config["decoder_high_res_0_shape"]),
        torch.randn(config["decoder_high_res_1_shape"]),
    )
    ep_decoder = torch.export.export(decoder_wrapper, dummy_dec_args)
    decoder_pt2_path = output_dir / "edgetam_decoder_fp32.pt2"
    torch.export.save(ep_decoder, str(decoder_pt2_path))
    decoder_pt2_sha256 = sha256_file(decoder_pt2_path)
    decoder_pt2_size = decoder_pt2_path.stat().st_size
    print(f"Mask decoder saved to {decoder_pt2_path} ({decoder_pt2_size} bytes, SHA-256: {decoder_pt2_sha256[:16]}...)")

    # 4. Optional ONNX export
    onnx_vision_info: dict[str, Any] | None = None
    onnx_memory_info: dict[str, Any] | None = None
    onnx_decoder_info: dict[str, Any] | None = None
    if export_onnx:
        if importlib.util.find_spec("onnx") is not None and importlib.util.find_spec("onnxscript") is not None:
            try:
                print("Exporting Vision Encoder to ONNX opset 18...")
                onnx_vis_path = output_dir / "edgetam_vision_fp32.onnx"
                torch.onnx.export(
                    vision_wrapper,
                    (dummy_vision,),
                    str(onnx_vis_path),
                    input_names=["pixel_values"],
                    output_names=["fpn_0", "fpn_1", "fpn_2"],
                    opset_version=18,
                )
                onnx_vision_info = {
                    "path": onnx_vis_path.relative_to(ROOT).as_posix(),
                    "sha256": sha256_file(onnx_vis_path),
                    "bytes": onnx_vis_path.stat().st_size,
                }
            except Exception as exc:
                onnx_vision_info = {"status": "failed", "error": str(exc)}

            try:
                print("Exporting Memory Encoder to ONNX opset 18...")
                onnx_mem_path = output_dir / "edgetam_memory_fp32.onnx"
                torch.onnx.export(
                    memory_wrapper,
                    (dummy_pix, dummy_mask),
                    str(onnx_mem_path),
                    input_names=["pix_feat", "mask"],
                    output_names=["mem_features", "mem_pos"],
                    opset_version=18,
                )
                onnx_memory_info = {
                    "path": onnx_mem_path.relative_to(ROOT).as_posix(),
                    "sha256": sha256_file(onnx_mem_path),
                    "bytes": onnx_mem_path.stat().st_size,
                }
            except Exception as exc:
                onnx_memory_info = {"status": "failed", "error": str(exc)}

            try:
                print("Exporting Mask Decoder to ONNX opset 18...")
                onnx_dec_path = output_dir / "edgetam_decoder_fp32.onnx"
                torch.onnx.export(
                    decoder_wrapper,
                    dummy_dec_args,
                    str(onnx_dec_path),
                    input_names=[
                        "image_embeddings",
                        "image_positional_embeddings",
                        "sparse_prompt_embeddings",
                        "dense_prompt_embeddings",
                        "high_res_0",
                        "high_res_1",
                    ],
                    output_names=["masks", "iou_pred", "object_score_logits"],
                    opset_version=18,
                )
                onnx_decoder_info = {
                    "path": onnx_dec_path.relative_to(ROOT).as_posix(),
                    "sha256": sha256_file(onnx_dec_path),
                    "bytes": onnx_dec_path.stat().st_size,
                }
            except Exception as exc:
                onnx_decoder_info = {"status": "failed", "error": str(exc)}
        else:
            onnx_vision_info = {"status": "skipped", "reason": "onnx or onnxscript not installed"}
            onnx_memory_info = {"status": "skipped", "reason": "onnx or onnxscript not installed"}
            onnx_decoder_info = {"status": "skipped", "reason": "onnx or onnxscript not installed"}

    # 5. Parity Verification on Synthetic Tensors
    print("Verifying numerical output parity on synthetic tensors...")
    loaded_ep_vision = torch.export.load(str(vision_pt2_path))
    loaded_ep_memory = torch.export.load(str(memory_pt2_path))
    loaded_ep_decoder = torch.export.load(str(decoder_pt2_path))
    exported_vision = loaded_ep_vision.module()
    exported_memory = loaded_ep_memory.module()
    exported_decoder = loaded_ep_decoder.module()

    torch.manual_seed(42)
    syn_vision_in = torch.randn(config["vision_input_shape"])
    with torch.no_grad():
        ref_vis_outs = vision_wrapper(syn_vision_in)
        exp_vis_outs = exported_vision(syn_vision_in)

    vis_parity_synthetic = compute_feature_parity(
        [t.numpy() for t in ref_vis_outs],
        [t.numpy() for t in exp_vis_outs],
        max_abs_diff=config["max_abs_diff"],
    )

    torch.manual_seed(123)
    syn_pix_in = torch.randn(config["memory_pix_shape"])
    syn_mask_in = torch.randn(config["memory_mask_shape"])
    with torch.no_grad():
        ref_mem_outs = memory_wrapper(syn_pix_in, syn_mask_in)
        exp_mem_outs = exported_memory(syn_pix_in, syn_mask_in)

    mem_parity_synthetic = compute_feature_parity(
        [t.numpy() for t in ref_mem_outs],
        [t.numpy() for t in exp_mem_outs],
        max_abs_diff=config["max_abs_diff"],
    )

    torch.manual_seed(456)
    syn_dec_args = (
        torch.randn(config["decoder_emb_shape"]),
        torch.randn(config["decoder_emb_shape"]),
        torch.randn(config["decoder_sparse_shape"]),
        torch.randn(config["decoder_emb_shape"]),
        torch.randn(config["decoder_high_res_0_shape"]),
        torch.randn(config["decoder_high_res_1_shape"]),
    )
    with torch.no_grad():
        ref_dec_outs = decoder_wrapper(*syn_dec_args)
        exp_dec_outs = exported_decoder(*syn_dec_args)

    dec_parity_synthetic = compute_feature_parity(
        [t.numpy() for t in ref_dec_outs],
        [t.numpy() for t in exp_dec_outs],
        max_abs_diff=config["max_abs_diff"],
    )

    # 6. Parity on Real Validation Frames (if available)
    vis_parity_real: dict[str, Any] | None = None
    if dataset_dir is not None and val_coco is not None:
        val_img_paths = [dataset_dir.resolve() / img["file_name"] for img in val_coco.get("images", [])[:4]]
        if val_img_paths:
            import cv2
            real_tensors = []
            for p in val_img_paths:
                img = cv2.imread(str(p))
                if img is not None:
                    resized = cv2.resize(img, (config["vision_input_shape"][3], config["vision_input_shape"][2]))
                    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).transpose(2, 0, 1)
                    real_tensors.append(torch.from_numpy(rgb).unsqueeze(0).float() / 255.0)

            if real_tensors:
                diffs = []
                for t in real_tensors:
                    with torch.no_grad():
                        r_out = vision_wrapper(t)
                        e_out = exported_vision(t)
                    par = compute_feature_parity(
                        [o.numpy() for o in r_out],
                        [o.numpy() for o in e_out],
                        max_abs_diff=config["max_abs_diff"],
                    )
                    diffs.append(par["overall_max_abs_diff"])

                vis_parity_real = {
                    "frames_tested": len(real_tensors),
                    "passed": bool(max(diffs) <= config["max_abs_diff"]),
                    "overall_max_abs_diff": max(diffs),
                }

    # 7. Diagnostic Desktop Latency Measurements
    print("Measuring diagnostic desktop forward-pass latencies...")
    with torch.no_grad():
        vis_cpu_timings = _measure_latency(lambda: exported_vision(syn_vision_in), repeats=5)
        mem_cpu_timings = _measure_latency(lambda: exported_memory(syn_pix_in, syn_mask_in), repeats=5)
        dec_cpu_timings = _measure_latency(lambda: exported_decoder(*syn_dec_args), repeats=5)

    all_passed = bool(
        vis_parity_synthetic["passed"]
        and mem_parity_synthetic["passed"]
        and dec_parity_synthetic["passed"]
        and (vis_parity_real["passed"] if vis_parity_real is not None else True)
    )

    manifest: dict[str, Any] = {
        "harness_id": HARNESS_ID,
        "model": {
            "checkpoint": EDGETAM_CHECKPOINT,
            "revision": EDGETAM_REVISION,
            "license": "Apache-2.0",
        },
        "git_commit": git_commit_sha(repo_root),
        "system": _system_metadata(),
        "config": config,
        "exported_subgraphs": {
            "vision_encoder": {
                "file": vision_pt2_path.relative_to(ROOT).as_posix(),
                "bytes": vision_pt2_size,
                "sha256": vision_pt2_sha256,
                "input_shape": config["vision_input_shape"],
                "outputs": [f"fpn_{i} {list(t.shape)}" for i, t in enumerate(ref_vis_outs)],
                "onnx": onnx_vision_info,
                "parity_synthetic": vis_parity_synthetic,
                "parity_real_frames": vis_parity_real,
                "diagnostic_latency_cpu": vis_cpu_timings,
            },
            "memory_encoder": {
                "file": memory_pt2_path.relative_to(ROOT).as_posix(),
                "bytes": memory_pt2_size,
                "sha256": memory_pt2_sha256,
                "input_shapes": {
                    "pix_feat": config["memory_pix_shape"],
                    "mask": config["memory_mask_shape"],
                },
                "outputs": [f"{name} {list(t.shape)}" for name, t in zip(("mem_features", "mem_pos"), ref_mem_outs)],
                "onnx": onnx_memory_info,
                "parity_synthetic": mem_parity_synthetic,
                "diagnostic_latency_cpu": mem_cpu_timings,
            },
            "mask_decoder": {
                "file": decoder_pt2_path.relative_to(ROOT).as_posix(),
                "bytes": decoder_pt2_size,
                "sha256": decoder_pt2_sha256,
                "input_shapes": {
                    "image_embeddings": config["decoder_emb_shape"],
                    "image_positional_embeddings": config["decoder_emb_shape"],
                    "sparse_prompt_embeddings": config["decoder_sparse_shape"],
                    "dense_prompt_embeddings": config["decoder_emb_shape"],
                    "high_res_0": config["decoder_high_res_0_shape"],
                    "high_res_1": config["decoder_high_res_1_shape"],
                },
                "outputs": [f"{name} {list(t.shape)}" for name, t in zip(("masks", "iou_pred", "object_score_logits"), ref_dec_outs)],
                "onnx": onnx_decoder_info,
                "parity_synthetic": dec_parity_synthetic,
                "diagnostic_latency_cpu": dec_cpu_timings,
            },
        },
        "passed": all_passed,
    }

    manifest_path = output_dir / "export_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Export manifest saved to {manifest_path} (passed: {all_passed})")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=label_package.PRIVATE_ROOT / "edgetam-export",
        help="Output directory under validation/private/ for exported graphs and manifest",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=ROOT,
        help="Repository root (defaults to parent of research/)",
    )
    parser.add_argument(
        "--input-height-px",
        type=int,
        default=DEFAULT_INPUT_HEIGHT_PX,
        help=f"Static input height in pixels (default: {DEFAULT_INPUT_HEIGHT_PX})",
    )
    parser.add_argument(
        "--input-width-px",
        type=int,
        default=DEFAULT_INPUT_WIDTH_PX,
        help=f"Static input width in pixels (default: {DEFAULT_INPUT_WIDTH_PX})",
    )
    parser.add_argument(
        "--max-abs-diff",
        type=float,
        default=DEFAULT_MAX_ABS_DIFF,
        help=f"Maximum acceptable absolute numerical difference against eager PyTorch (default: {DEFAULT_MAX_ABS_DIFF})",
    )
    parser.add_argument(
        "--onnx",
        action="store_true",
        help="Also export ONNX format if onnx and onnxscript are installed",
    )
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=None,
        help="Optional curated Phase B dataset; admission checks reject held-out fixtures before reading frames",
    )
    args = parser.parse_args()

    result = export_edgetam_tracker(
        output_dir=args.output_dir,
        repo_root=args.repo_root,
        input_height_px=args.input_height_px,
        input_width_px=args.input_width_px,
        max_abs_diff=args.max_abs_diff,
        export_onnx=args.onnx,
        dataset_dir=args.dataset_dir,
    )
    if not result.get("passed"):
        sys.exit(1)


if __name__ == "__main__":
    main()
