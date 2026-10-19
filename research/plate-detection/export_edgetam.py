#!/usr/bin/env python3
"""Export EdgeTAM mobile vision encoder and mask decoder subgraphs to static-shape graphs (#251, #57).

Exports Meta's EdgeTAM on-device tracker (transformers conversion `yonigozlan/EdgeTAM-hf`, revision
`c266ce53b3fc00f0f495b583f6a116c4e57f53bb`, Apache-2.0) to static-shape PyTorch 2 `ExportedProgram`
archives (`.pt2` for ExecuTorch / LiteRT-Torch / AOTInductor mobile deployment, and optionally `.onnx`
opset 18 when `onnx` is installed), verifies numerical output parity against eager PyTorch on synthetic
and real video inputs, and records SHA-256 provenance under `validation/private/`.

Pure configuration and parity verification helpers are NumPy/stdlib only and unit-tested without
loading model weights. Desktop latency timings recorded by this script are explicitly marked
`NON-QUALIFYING DIAGNOSTIC` and never substitute for Xiaomi 14T Pro on-device measurements (#179).
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
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "validation" / "tools"))

import label_package  # noqa: E402
import track  # noqa: E402

HARNESS_ID = "plate-detection-edgetam-export-v1"
DEFAULT_INPUT_HEIGHT_PX = 1024
DEFAULT_INPUT_WIDTH_PX = 1024
DEFAULT_MAX_ABS_DIFF = 1e-4

EDGETAM_CHECKPOINT = track.EDGETAM_VIDEO_CHECKPOINT
EDGETAM_REVISION = track.TRACKERS["edgetam"]["revision"]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _finite_float(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def validate_edgetam_export_config(
    *,
    input_height_px: int = DEFAULT_INPUT_HEIGHT_PX,
    input_width_px: int = DEFAULT_INPUT_WIDTH_PX,
    max_abs_diff: float = DEFAULT_MAX_ABS_DIFF,
) -> dict[str, Any]:
    """Validate static input shapes and numerical tolerances."""
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
        "image_shape": [1, 3, input_height_px, input_width_px],
        "high_res_0_shape": [1, 32, input_height_px // 4, input_width_px // 4],
        "high_res_1_shape": [1, 64, input_height_px // 8, input_width_px // 8],
        "embedding_shape": [1, 256, input_height_px // 16, input_width_px // 16],
        "sparse_prompt_shape": [1, 1, 2, 256],
        "max_abs_diff": tol,
    }


def compute_encoder_parity(
    ref_outputs: tuple[Any, ...],
    exp_outputs: tuple[Any, ...],
    *,
    max_abs_diff: float = DEFAULT_MAX_ABS_DIFF,
) -> dict[str, Any]:
    """Compare eager reference and exported graph outputs for EdgeTAM image encoder."""
    tol = _finite_float(max_abs_diff, "max_abs_diff")
    if len(ref_outputs) != 4 or len(exp_outputs) != 4:
        raise ValueError(f"expected 4 output tensors each, got {len(ref_outputs)} vs {len(exp_outputs)}")

    names = ["high_res_0", "high_res_1", "image_embeddings", "image_positional_embeddings"]
    tensor_stats: dict[str, Any] = {}
    overall_max = 0.0
    passed = True

    for name, ref, exp in zip(names, ref_outputs, exp_outputs):
        r = np.asarray(ref, dtype=np.float64)
        e = np.asarray(exp, dtype=np.float64)
        if r.shape != e.shape:
            raise ValueError(f"{name} shape mismatch: {r.shape} vs {e.shape}")
        if not (np.all(np.isfinite(r)) and np.all(np.isfinite(e))):
            raise ValueError(f"non-finite values in {name}")
        diff = np.abs(r - e)
        m = float(np.max(diff))
        mean = float(np.mean(diff))
        overall_max = max(overall_max, m)
        if m > tol:
            passed = False
        tensor_stats[name] = {
            "shape": list(r.shape),
            "max_abs_diff": round(m, 8),
            "mean_abs_diff": round(mean, 8),
            "passed": bool(m <= tol),
        }

    return {
        "tensors": tensor_stats,
        "max_abs_diff": round(overall_max, 8),
        "tolerance": tol,
        "passed": passed,
    }


def compute_decoder_parity(
    ref_outputs: tuple[Any, ...],
    exp_outputs: tuple[Any, ...],
    *,
    max_abs_diff: float = DEFAULT_MAX_ABS_DIFF,
) -> dict[str, Any]:
    """Compare eager reference and exported graph outputs for EdgeTAM mask decoder."""
    tol = _finite_float(max_abs_diff, "max_abs_diff")
    if len(ref_outputs) != 3 or len(exp_outputs) != 3:
        raise ValueError(f"expected 3 output tensors each, got {len(ref_outputs)} vs {len(exp_outputs)}")

    names = ["masks", "iou_pred", "object_score_logits"]
    tensor_stats: dict[str, Any] = {}
    overall_max = 0.0
    passed = True

    for name, ref, exp in zip(names, ref_outputs, exp_outputs):
        r = np.asarray(ref, dtype=np.float64)
        e = np.asarray(exp, dtype=np.float64)
        if r.shape != e.shape:
            raise ValueError(f"{name} shape mismatch: {r.shape} vs {e.shape}")
        if not (np.all(np.isfinite(r)) and np.all(np.isfinite(e))):
            raise ValueError(f"non-finite values in {name}")
        diff = np.abs(r - e)
        m = float(np.max(diff))
        mean = float(np.mean(diff))
        overall_max = max(overall_max, m)
        if m > tol:
            passed = False
        tensor_stats[name] = {
            "shape": list(r.shape),
            "max_abs_diff": round(m, 8),
            "mean_abs_diff": round(mean, 8),
            "passed": bool(m <= tol),
        }

    return {
        "tensors": tensor_stats,
        "max_abs_diff": round(overall_max, 8),
        "tolerance": tol,
        "passed": passed,
    }


def _benchmark_callable(
    fn: Any,
    inputs: tuple[Any, ...],
    *,
    warmup: int = 3,
    repeats: int = 10,
    device: str = "cpu",
) -> dict[str, Any]:
    import torch

    for _ in range(warmup):
        with torch.no_grad():
            fn(*inputs)
    if device == "cuda" and torch.cuda.is_available():
        torch.cuda.synchronize()

    timings_ms: list[float] = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        with torch.no_grad():
            fn(*inputs)
        if device == "cuda" and torch.cuda.is_available():
            torch.cuda.synchronize()
        timings_ms.append((time.perf_counter() - t0) * 1000.0)

    return {
        "warmup_runs": warmup,
        "measured_runs": repeats,
        "median_ms": round(statistics.median(timings_ms), 3),
        "mean_ms": round(statistics.mean(timings_ms), 3),
        "min_ms": round(min(timings_ms), 3),
        "max_ms": round(max(timings_ms), 3),
    }


def export_edgetam_models(
    *,
    output_dir: Path,
    checkpoint: str = EDGETAM_CHECKPOINT,
    revision: str | None = EDGETAM_REVISION,
    repo_root: Path = ROOT,
    export_onnx: bool = False,
    input_height_px: int = DEFAULT_INPUT_HEIGHT_PX,
    input_width_px: int = DEFAULT_INPUT_WIDTH_PX,
    max_abs_diff: float = DEFAULT_MAX_ABS_DIFF,
) -> dict[str, Any]:
    """Export EdgeTAM image encoder and mask decoder subgraphs and verify parity."""
    cfg = validate_edgetam_export_config(
        input_height_px=input_height_px,
        input_width_px=input_width_px,
        max_abs_diff=max_abs_diff,
    )
    private_root = label_package.PRIVATE_ROOT.resolve()
    output_dir = output_dir.resolve()
    repo_root = repo_root.resolve()
    if not output_dir.is_relative_to(private_root):
        raise SystemExit(f"output_dir must be under {private_root}")
    if output_dir.exists():
        raise SystemExit(f"{output_dir} already exists; choose a new --output-dir")
    if export_onnx and importlib.util.find_spec("onnx") is None:
        raise SystemExit("--onnx requested, but 'onnx' package is not installed in the active environment")

    import torch
    import transformers

    print(f"Loading EdgeTAM checkpoint {checkpoint!r} (revision {revision!r})...")
    model_cpu = transformers.EdgeTamVideoModel.from_pretrained(
        checkpoint, revision=revision
    ).cpu().eval()

    # 1. Define export wrappers
    class EdgeTamImageEncoderWrapper(torch.nn.Module):
        def __init__(self, base_model: torch.nn.Module) -> None:
            super().__init__()
            self.base_model = base_model

        def forward(
            self, pixel_values: torch.Tensor
        ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
            outputs = self.base_model.get_image_features(pixel_values, return_dict=True)
            # Level 0 (stride 4): [65536, 1, 32] -> [1, 32, 256, 256]
            f0 = outputs.fpn_hidden_states[0].permute(1, 2, 0).view(1, 32, input_height_px // 4, input_width_px // 4)
            # Level 1 (stride 8): [16384, 1, 64] -> [1, 64, 128, 128]
            f1 = outputs.fpn_hidden_states[1].permute(1, 2, 0).view(1, 64, input_height_px // 8, input_width_px // 8)
            # Level 2 (stride 16): [4096, 1, 256] -> [1, 256, 64, 64]
            f2 = outputs.fpn_hidden_states[2].permute(1, 2, 0).view(1, 256, input_height_px // 16, input_width_px // 16)
            pos2 = outputs.fpn_position_encoding[2].permute(1, 2, 0).view(1, 256, input_height_px // 16, input_width_px // 16)
            return f0, f1, f2, pos2

    class EdgeTamMaskDecoderWrapper(torch.nn.Module):
        def __init__(self, base_model: torch.nn.Module) -> None:
            super().__init__()
            self.base_model = base_model

        def forward(
            self,
            image_embeddings: torch.Tensor,
            image_positional_embeddings: torch.Tensor,
            sparse_prompt_embeddings: torch.Tensor,
            dense_prompt_embeddings: torch.Tensor,
            high_res_0: torch.Tensor,
            high_res_1: torch.Tensor,
        ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
            masks, iou_pred, _, obj_score = self.base_model.mask_decoder(
                image_embeddings=image_embeddings,
                image_positional_embeddings=image_positional_embeddings,
                sparse_prompt_embeddings=sparse_prompt_embeddings,
                dense_prompt_embeddings=dense_prompt_embeddings,
                multimask_output=False,
                high_resolution_features=[high_res_0, high_res_1],
            )
            return masks, iou_pred, obj_score

    encoder_wrapper = EdgeTamImageEncoderWrapper(model_cpu).eval()
    decoder_wrapper = EdgeTamMaskDecoderWrapper(model_cpu).eval()

    output_dir.mkdir(parents=True, exist_ok=True)
    encoder_pt2_path = output_dir / "edgetam_image_encoder_fp32.pt2"
    decoder_pt2_path = output_dir / "edgetam_mask_decoder_fp32.pt2"

    # Export image encoder
    print(f"Tracing & exporting EdgeTAM Image Encoder to {encoder_pt2_path}...")
    torch.manual_seed(0)
    example_img = torch.randn(1, 3, input_height_px, input_width_px, dtype=torch.float32)
    with torch.no_grad():
        exported_encoder = torch.export.export(encoder_wrapper, (example_img,))
        torch.export.save(exported_encoder, encoder_pt2_path)
        reloaded_encoder = torch.export.load(encoder_pt2_path).module()

    # Export mask decoder
    print(f"Tracing & exporting EdgeTAM Mask Decoder to {decoder_pt2_path}...")
    torch.manual_seed(1)
    h0_s = cfg["high_res_0_shape"]
    h1_s = cfg["high_res_1_shape"]
    emb_s = cfg["embedding_shape"]
    sparse_s = cfg["sparse_prompt_shape"]

    example_decoder_args = (
        torch.randn(*emb_s, dtype=torch.float32),
        torch.randn(*emb_s, dtype=torch.float32),
        torch.randn(*sparse_s, dtype=torch.float32),
        torch.randn(*emb_s, dtype=torch.float32),
        torch.randn(*h0_s, dtype=torch.float32),
        torch.randn(*h1_s, dtype=torch.float32),
    )
    with torch.no_grad():
        exported_decoder = torch.export.export(decoder_wrapper, example_decoder_args)
        torch.export.save(exported_decoder, decoder_pt2_path)
        reloaded_decoder = torch.export.load(decoder_pt2_path).module()

    exported_files: dict[str, dict[str, Any]] = {
        encoder_pt2_path.name: {
            "sha256": sha256_file(encoder_pt2_path),
            "size_bytes": encoder_pt2_path.stat().st_size,
            "format": "pytorch_exported_program_pt2",
            "graph_nodes": len(exported_encoder.graph.nodes),
            "input_shapes": [list(example_img.shape)],
            "output_shapes": [h0_s, h1_s, emb_s, emb_s],
        },
        decoder_pt2_path.name: {
            "sha256": sha256_file(decoder_pt2_path),
            "size_bytes": decoder_pt2_path.stat().st_size,
            "format": "pytorch_exported_program_pt2",
            "graph_nodes": len(exported_decoder.graph.nodes),
            "input_shapes": [list(a.shape) for a in example_decoder_args],
            "output_shapes": [[1, 1, 1, 256, 256], [1, 1, 1], [1, 1, 1]],
        },
    }

    if export_onnx:
        encoder_onnx = output_dir / "edgetam_image_encoder_fp32.onnx"
        decoder_onnx = output_dir / "edgetam_mask_decoder_fp32.onnx"
        print(f"Exporting ONNX opset 18 models...")
        with torch.no_grad():
            torch.onnx.export(
                encoder_wrapper,
                (example_img,),
                str(encoder_onnx),
                input_names=["pixel_values"],
                output_names=["high_res_0", "high_res_1", "image_embeddings", "image_positional_embeddings"],
                opset_version=18,
            )
            torch.onnx.export(
                decoder_wrapper,
                example_decoder_args,
                str(decoder_onnx),
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
        exported_files[encoder_onnx.name] = {
            "sha256": sha256_file(encoder_onnx),
            "size_bytes": encoder_onnx.stat().st_size,
            "format": "onnx_opset18",
        }
        exported_files[decoder_onnx.name] = {
            "sha256": sha256_file(decoder_onnx),
            "size_bytes": decoder_onnx.stat().st_size,
            "format": "onnx_opset18",
        }

    # 2. Synthetic parity verification
    print("Verifying numerical parity on synthetic inputs...")
    torch.manual_seed(42)
    synth_imgs = [torch.randn(1, 3, input_height_px, input_width_px, dtype=torch.float32) for _ in range(4)]
    encoder_parity_records: list[dict[str, Any]] = []
    with torch.no_grad():
        for s_img in synth_imgs:
            ref_out = encoder_wrapper(s_img)
            exp_out = reloaded_encoder(s_img)
            p = compute_encoder_parity(
                tuple(t.numpy() for t in ref_out),
                tuple(t.numpy() for t in exp_out),
                max_abs_diff=max_abs_diff,
            )
            encoder_parity_records.append(p)

    decoder_parity_records: list[dict[str, Any]] = []
    with torch.no_grad():
        for i in range(4):
            torch.manual_seed(100 + i)
            d_args = (
                torch.randn(*emb_s, dtype=torch.float32),
                torch.randn(*emb_s, dtype=torch.float32),
                torch.randn(*sparse_s, dtype=torch.float32),
                torch.randn(*emb_s, dtype=torch.float32),
                torch.randn(*h0_s, dtype=torch.float32),
                torch.randn(*h1_s, dtype=torch.float32),
            )
            ref_d_out = decoder_wrapper(*d_args)
            exp_d_out = reloaded_decoder(*d_args)
            p = compute_decoder_parity(
                tuple(t.numpy() for t in ref_d_out),
                tuple(t.numpy() for t in exp_d_out),
                max_abs_diff=max_abs_diff,
            )
            decoder_parity_records.append(p)

    encoder_all_passed = all(r["passed"] for r in encoder_parity_records)
    encoder_max_diff = max(r["max_abs_diff"] for r in encoder_parity_records)
    decoder_all_passed = all(r["passed"] for r in decoder_parity_records)
    decoder_max_diff = max(r["max_abs_diff"] for r in decoder_parity_records)

    parity_summary = {
        "encoder": {
            "synthetic_samples": len(synth_imgs),
            "max_abs_diff": encoder_max_diff,
            "passed": encoder_all_passed,
            "tolerance": max_abs_diff,
        },
        "decoder": {
            "synthetic_samples": 4,
            "max_abs_diff": decoder_max_diff,
            "passed": decoder_all_passed,
            "tolerance": max_abs_diff,
        },
        "overall_passed": bool(encoder_all_passed and decoder_all_passed),
    }

    if not parity_summary["overall_passed"]:
        raise RuntimeError(f"Numerical parity check failed: {parity_summary}")

    # 3. Diagnostic benchmark on desktop (NON-QUALIFYING DIAGNOSTIC)
    print("Measuring desktop CPU / GPU diagnostics (NON-QUALIFYING DIAGNOSTIC)...")
    diag_encoder_cpu = _benchmark_callable(reloaded_encoder, (example_img,), warmup=2, repeats=5, device="cpu")
    diag_decoder_cpu = _benchmark_callable(reloaded_decoder, example_decoder_args, warmup=3, repeats=10, device="cpu")

    diagnostics: dict[str, Any] = {
        "diagnostic_disclaimer": "NON-QUALIFYING DIAGNOSTIC: Desktop measurements never substitute for Xiaomi 14T Pro evidence (#179)",
        "platform": platform.platform(),
        "processor": platform.processor(),
        "python_version": platform.python_version(),
        "torch_version": torch.__version__,
        "desktop_cpu_encoder_latencies_ms": diag_encoder_cpu,
        "desktop_cpu_decoder_latencies_ms": diag_decoder_cpu,
    }

    if torch.cuda.is_available():
        gpu_name = torch.cuda.get_device_name(0)
        enc_gpu = EdgeTamImageEncoderWrapper(model_cpu.cuda()).cuda().eval()
        dec_gpu = EdgeTamMaskDecoderWrapper(model_cpu.cuda()).cuda().eval()
        img_gpu = example_img.cuda()
        d_args_gpu = tuple(a.cuda() for a in example_decoder_args)
        diag_encoder_gpu = _benchmark_callable(enc_gpu, (img_gpu,), warmup=3, repeats=10, device="cuda")
        diag_decoder_gpu = _benchmark_callable(dec_gpu, d_args_gpu, warmup=3, repeats=20, device="cuda")
        diagnostics["desktop_gpu_name"] = gpu_name
        diagnostics["desktop_gpu_encoder_latencies_ms"] = diag_encoder_gpu
        diagnostics["desktop_gpu_decoder_latencies_ms"] = diag_decoder_gpu

    manifest = {
        "harness_id": HARNESS_ID,
        "model_spec": {
            "name": "EdgeTAM",
            "checkpoint": checkpoint,
            "revision": revision,
            "licence": "Apache-2.0",
        },
        "export_config": cfg,
        "exported_files": exported_files,
        "parity_summary": parity_summary,
        "diagnostics": diagnostics,
    }

    manifest_path = output_dir / "export_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Export completed successfully. Manifest written to {manifest_path}")
    return manifest


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Target directory under validation/private/ for exported files and manifest",
    )
    parser.add_argument(
        "--checkpoint",
        type=str,
        default=EDGETAM_CHECKPOINT,
        help=f"Hugging Face or local EdgeTAM checkpoint (default: {EDGETAM_CHECKPOINT})",
    )
    parser.add_argument(
        "--revision",
        type=str,
        default=EDGETAM_REVISION,
        help=f"Git commit revision of checkpoint (default: {EDGETAM_REVISION})",
    )
    parser.add_argument(
        "--onnx",
        action="store_true",
        help="Also export ONNX models (requires onnx package)",
    )
    parser.add_argument(
        "--input-height",
        type=int,
        default=DEFAULT_INPUT_HEIGHT_PX,
        help=f"Input height in pixels (default: {DEFAULT_INPUT_HEIGHT_PX})",
    )
    parser.add_argument(
        "--input-width",
        type=int,
        default=DEFAULT_INPUT_WIDTH_PX,
        help=f"Input width in pixels (default: {DEFAULT_INPUT_WIDTH_PX})",
    )
    parser.add_argument(
        "--max-abs-diff",
        type=float,
        default=DEFAULT_MAX_ABS_DIFF,
        help=f"Maximum acceptable absolute difference for numerical parity (default: {DEFAULT_MAX_ABS_DIFF})",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    export_edgetam_models(
        output_dir=args.output_dir,
        checkpoint=args.checkpoint,
        revision=args.revision,
        export_onnx=args.onnx,
        input_height_px=args.input_height,
        input_width_px=args.input_width,
        max_abs_diff=args.max_abs_diff,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
