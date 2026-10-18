#!/usr/bin/env python3
"""Export Phase B student plate detector to static-shape mobile graph and verify parity (#251, #233).

Exports a fine-tuned ``DFineForObjectDetection`` checkpoint to a static-shape PyTorch 2
``ExportedProgram`` archive (``.pt2`` for ExecuTorch / LiteRT-Torch / AOTInductor mobile deployment,
and optionally ``.onnx`` when ``onnx`` is installed), verifies numerical output parity against eager
PyTorch on synthetic and session-disjoint validation frames, and records SHA-256 provenance under
``validation/private/``.

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
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "validation" / "tools"))

import detectors  # noqa: E402
import label_package  # noqa: E402
import train_phase_b  # noqa: E402

HARNESS_ID = "plate-detection-phase-b-export-v1"
DEFAULT_INPUT_HEIGHT_PX = 640
DEFAULT_INPUT_WIDTH_PX = 640
DEFAULT_MAX_LOGIT_ABS_DIFF = 1e-4
DEFAULT_MAX_BOX_ABS_DIFF = 1e-4


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
    max_logit_abs_diff: float = DEFAULT_MAX_LOGIT_ABS_DIFF,
    max_box_abs_diff: float = DEFAULT_MAX_BOX_ABS_DIFF,
) -> dict[str, Any]:
    """Validate static input shape and parity tolerances."""
    if isinstance(input_height_px, bool) or not isinstance(input_height_px, int) or input_height_px < 32 or input_height_px % 32 != 0:
        raise ValueError("input_height_px must be a positive integer multiple of 32")
    if isinstance(input_width_px, bool) or not isinstance(input_width_px, int) or input_width_px < 32 or input_width_px % 32 != 0:
        raise ValueError("input_width_px must be a positive integer multiple of 32")
    logit_tol = _finite_float(max_logit_abs_diff, "max_logit_abs_diff")
    box_tol = _finite_float(max_box_abs_diff, "max_box_abs_diff")
    if logit_tol <= 0.0:
        raise ValueError("max_logit_abs_diff must be positive")
    if box_tol <= 0.0:
        raise ValueError("max_box_abs_diff must be positive")
    return {
        "input_shape": [1, 3, input_height_px, input_width_px],
        "max_logit_abs_diff": logit_tol,
        "max_box_abs_diff": box_tol,
    }


def _cxcywh_to_xyxy(box: np.ndarray) -> list[float]:
    cx, cy, w, h = (float(box[0]), float(box[1]), float(box[2]), float(box[3]))
    return [cx - 0.5 * w, cy - 0.5 * h, cx + 0.5 * w, cy + 0.5 * h]


def compute_tensor_parity(
    ref_logits: Any,
    ref_boxes: Any,
    exp_logits: Any,
    exp_boxes: Any,
    *,
    max_logit_abs_diff: float = DEFAULT_MAX_LOGIT_ABS_DIFF,
    max_box_abs_diff: float = DEFAULT_MAX_BOX_ABS_DIFF,
) -> dict[str, Any]:
    """Compare eager reference `(logits, pred_boxes)` against exported graph outputs."""
    validate_export_config(
        max_logit_abs_diff=max_logit_abs_diff,
        max_box_abs_diff=max_box_abs_diff,
    )
    rl = np.asarray(ref_logits, dtype=np.float64)
    rb = np.asarray(ref_boxes, dtype=np.float64)
    el = np.asarray(exp_logits, dtype=np.float64)
    eb = np.asarray(exp_boxes, dtype=np.float64)

    if rl.shape != el.shape or rl.ndim != 3:
        raise ValueError(f"logits shape mismatch or invalid rank: {rl.shape} vs {el.shape}")
    if rb.shape != eb.shape or rb.ndim != 3 or rb.shape[2] != 4 or rb.shape[:2] != rl.shape[:2]:
        raise ValueError(f"pred_boxes shape mismatch: {rb.shape} vs {eb.shape} (logits {rl.shape})")
    if not (np.all(np.isfinite(rl)) and np.all(np.isfinite(rb)) and np.all(np.isfinite(el)) and np.all(np.isfinite(eb))):
        raise ValueError("non-finite values in reference or exported tensors")

    logit_diff = np.abs(rl - el)
    box_diff = np.abs(rb - eb)
    logit_max = float(np.max(logit_diff))
    logit_mean = float(np.mean(logit_diff))
    box_max = float(np.max(box_diff))
    box_mean = float(np.mean(box_diff))

    batch_size = rl.shape[0]
    query_matches = 0
    top1_ious: list[float] = []
    for b_idx in range(batch_size):
        ref_q = int(np.argmax(np.max(rl[b_idx], axis=-1)))
        exp_q = int(np.argmax(np.max(el[b_idx], axis=-1)))
        if ref_q == exp_q:
            query_matches += 1
        iou = detectors.box_iou(_cxcywh_to_xyxy(rb[b_idx, ref_q]), _cxcywh_to_xyxy(eb[b_idx, exp_q]))
        top1_ious.append(iou)

    top1_match_rate = round(query_matches / batch_size, 4) if batch_size else None
    top1_iou_min = round(min(top1_ious), 6) if top1_ious else None
    passed = bool(
        logit_max <= max_logit_abs_diff
        and box_max <= max_box_abs_diff
        and query_matches == batch_size
    )
    return {
        "samples": int(batch_size),
        "num_queries": int(rl.shape[1]),
        "num_classes": int(rl.shape[2]),
        "logit_max_abs_diff": round(logit_max, 8),
        "logit_mean_abs_diff": round(logit_mean, 8),
        "box_max_abs_diff": round(box_max, 8),
        "box_mean_abs_diff": round(box_mean, 8),
        "top1_query_match_rate": top1_match_rate,
        "top1_box_iou_min": top1_iou_min,
        "max_logit_abs_diff_tolerance": max_logit_abs_diff,
        "max_box_abs_diff_tolerance": max_box_abs_diff,
        "passed": passed,
    }


def export_student_detector(
    *,
    checkpoint_dir: Path,
    output_dir: Path,
    dataset_dir: Path | None = None,
    repo_root: Path = ROOT,
    export_onnx: bool = False,
    input_height_px: int = DEFAULT_INPUT_HEIGHT_PX,
    input_width_px: int = DEFAULT_INPUT_WIDTH_PX,
    max_val_samples: int = 16,
    manifests: list[Path] | None = None,
) -> dict[str, Any]:
    """Export D-FINE-S student checkpoint to `.pt2` (and optional `.onnx`) and verify parity."""
    cfg = validate_export_config(input_height_px=input_height_px, input_width_px=input_width_px)
    private_root = label_package.PRIVATE_ROOT.resolve()
    checkpoint_dir = checkpoint_dir.resolve()
    output_dir = output_dir.resolve()
    repo_root = repo_root.resolve()
    if not checkpoint_dir.is_relative_to(private_root):
        raise SystemExit(f"checkpoint_dir must be under {private_root}")
    if not output_dir.is_relative_to(private_root):
        raise SystemExit(f"output_dir must be under {private_root}")
    if output_dir.exists():
        raise SystemExit(f"{output_dir} already exists; choose a new --output-dir")
    if export_onnx and importlib.util.find_spec("onnx") is None:
        raise SystemExit("--onnx requested, but 'onnx' package is not installed in the active environment")

    val_coco = None
    dataset_hashes = None
    if dataset_dir is not None:
        validated = train_phase_b.validate_dataset_dir(dataset_dir, repo_root, manifests=manifests)
        val_coco = validated["val_coco"]
        dataset_hashes = validated["hashes"]

    import cv2
    import torch
    from PIL import Image
    from transformers import AutoImageProcessor, DFineForObjectDetection

    class _StudentExportWrapper(torch.nn.Module):
        def __init__(self, base_model: torch.nn.Module) -> None:
            super().__init__()
            self.base_model = base_model

        def forward(self, pixel_values: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
            out = self.base_model(pixel_values=pixel_values)
            return out.logits, out.pred_boxes

    processor = AutoImageProcessor.from_pretrained(checkpoint_dir)
    model_cpu = DFineForObjectDetection.from_pretrained(checkpoint_dir).cpu().eval()
    wrapper_cpu = _StudentExportWrapper(model_cpu).eval()

    torch.manual_seed(0)
    example_input = torch.randn(1, 3, input_height_px, input_width_px, dtype=torch.float32)

    output_dir.mkdir(parents=True, exist_ok=True)
    pt2_path = output_dir / "dfine_s_student_fp32.pt2"
    with torch.no_grad():
        exported_program = torch.export.export(wrapper_cpu, (example_input,))
        torch.export.save(exported_program, pt2_path)
        reloaded_program = torch.export.load(pt2_path)
        reloaded_mod = reloaded_program.module()

    exported_files: dict[str, dict[str, Any]] = {
        pt2_path.name: {
            "sha256": sha256_file(pt2_path),
            "size_bytes": pt2_path.stat().st_size,
            "format": "pytorch_exported_program_pt2",
        }
    }

    if export_onnx:
        onnx_path = output_dir / "dfine_s_student_fp32.onnx"
        with torch.no_grad():
            torch.onnx.export(
                wrapper_cpu,
                (example_input,),
                str(onnx_path),
                input_names=["pixel_values"],
                output_names=["logits", "pred_boxes"],
                opset_version=18,
            )
        exported_files[onnx_path.name] = {
            "sha256": sha256_file(onnx_path),
            "size_bytes": onnx_path.stat().st_size,
            "format": "onnx_opset18",
        }

    # 1. Synthetic parity check (4 seeded random tensors)
    torch.manual_seed(42)
    synth_inputs = [torch.randn(1, 3, input_height_px, input_width_px, dtype=torch.float32) for _ in range(4)]
    ref_l_list, ref_b_list, exp_l_list, exp_b_list = [], [], [], []
    with torch.no_grad():
        for x in synth_inputs:
            rl, rb = wrapper_cpu(x)
            el, eb = reloaded_mod(x)
            ref_l_list.append(rl.numpy())
            ref_b_list.append(rb.numpy())
            exp_l_list.append(el.numpy())
            exp_b_list.append(eb.numpy())
    synthetic_parity = compute_tensor_parity(
        np.concatenate(ref_l_list, axis=0),
        np.concatenate(ref_b_list, axis=0),
        np.concatenate(exp_l_list, axis=0),
        np.concatenate(exp_b_list, axis=0),
    )
    if not synthetic_parity["passed"]:
        raise RuntimeError(f"synthetic parity check failed: {synthetic_parity}")

    # 2. Validation frame parity check + desktop CPU/CUDA diagnostic timings
    val_parity = None
    cpu_latencies_ms: list[float] = []
    cuda_latencies_ms: list[float] = []
    if val_coco is not None and dataset_dir is not None:
        val_images = list(val_coco.get("images") or [])[:max_val_samples]
        v_ref_l, v_ref_b, v_exp_l, v_exp_b = [], [], [], []
        with torch.no_grad():
            for img_entry in val_images:
                img_path = dataset_dir.resolve() / img_entry["file_name"]
                bgr = cv2.imread(str(img_path), cv2.IMREAD_COLOR)
                if bgr is None:
                    raise FileNotFoundError(f"missing validation frame {img_path}")
                pil = Image.fromarray(bgr[:, :, ::-1].copy())
                pixel_values = processor(images=pil, return_tensors="pt")["pixel_values"]
                rl, rb = wrapper_cpu(pixel_values)
                t0 = time.perf_counter()
                el, eb = reloaded_mod(pixel_values)
                cpu_latencies_ms.append((time.perf_counter() - t0) * 1000.0)
                v_ref_l.append(rl.numpy())
                v_ref_b.append(rb.numpy())
                v_exp_l.append(el.numpy())
                v_exp_b.append(eb.numpy())
        val_parity = compute_tensor_parity(
            np.concatenate(v_ref_l, axis=0),
            np.concatenate(v_ref_b, axis=0),
            np.concatenate(v_exp_l, axis=0),
            np.concatenate(v_exp_b, axis=0),
        )
        if not val_parity["passed"]:
            raise RuntimeError(f"validation parity check failed: {val_parity}")

        if torch.cuda.is_available():
            model_cuda = DFineForObjectDetection.from_pretrained(checkpoint_dir).to("cuda").eval()
            wrapper_cuda = _StudentExportWrapper(model_cuda).eval()
            with torch.no_grad():
                _ = wrapper_cuda(example_input.to("cuda"))
                torch.cuda.synchronize()
                for img_entry in val_images:
                    img_path = dataset_dir.resolve() / img_entry["file_name"]
                    bgr = cv2.imread(str(img_path), cv2.IMREAD_COLOR)
                    pil = Image.fromarray(bgr[:, :, ::-1].copy())
                    pv = processor(images=pil, return_tensors="pt")["pixel_values"].to("cuda")
                    torch.cuda.synchronize()
                    t0 = time.perf_counter()
                    _ = wrapper_cuda(pv)
                    torch.cuda.synchronize()
                    cuda_latencies_ms.append((time.perf_counter() - t0) * 1000.0)

    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=HERE, capture_output=True, text=True, check=False
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--", str(HERE)], cwd=HERE, capture_output=True, text=True, check=False
    ).stdout.strip()
    checkpoint_hashes = {
        p.name: sha256_file(p)
        for p in sorted(checkpoint_dir.glob("*"))
        if p.is_file()
    }
    manifest = {
        "harness": HARNESS_ID,
        "origin": "machine",
        "evidence_class": "NON-QUALIFYING DIAGNOSTIC",
        "code_commit": commit,
        "dirty": bool(dirty),
        "python": platform.python_version(),
        "source_sha256": {p.name: sha256_file(p) for p in sorted(HERE.glob("*.py"))},
        "export_config": cfg,
        "checkpoint_dir": str(checkpoint_dir.relative_to(repo_root)),
        "checkpoint_files_sha256": checkpoint_hashes,
        "dataset": dataset_hashes,
        "exported_files": exported_files,
        "parity": {
            "synthetic": synthetic_parity,
            "validation": val_parity,
        },
        "desktop_diagnostic_timings": {
            "note": "NON-QUALIFYING DIAGNOSTIC (desktop only; never substitutes for Xiaomi 14T Pro on-device measurements)",
            "cpu_exported_graph_ms_median": round(statistics.median(cpu_latencies_ms), 2) if cpu_latencies_ms else None,
            "cpu_exported_graph_ms_max": round(max(cpu_latencies_ms), 2) if cpu_latencies_ms else None,
            "cuda_forward_ms_median": round(statistics.median(cuda_latencies_ms), 2) if cuda_latencies_ms else None,
            "cuda_forward_ms_max": round(max(cuda_latencies_ms), 2) if cuda_latencies_ms else None,
            "samples": len(cpu_latencies_ms),
        },
    }
    (output_dir / "export_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return manifest


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--checkpoint-dir", type=Path, required=True, help="trained student best_checkpoint directory")
    p.add_argument("--output-dir", type=Path, required=True, help="new output directory under validation/private")
    p.add_argument("--dataset-dir", type=Path, help="optional Phase B dataset directory for validation parity check")
    p.add_argument("--repo-root", type=Path, default=ROOT)
    p.add_argument("--onnx", action="store_true", help="also export .onnx (requires onnx package)")
    p.add_argument("--input-height-px", type=int, default=DEFAULT_INPUT_HEIGHT_PX)
    p.add_argument("--input-width-px", type=int, default=DEFAULT_INPUT_WIDTH_PX)
    p.add_argument("--max-val-samples", type=int, default=16)
    p.add_argument("--manifest", type=Path, action="append")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    manifest = export_student_detector(
        checkpoint_dir=args.checkpoint_dir,
        output_dir=args.output_dir,
        dataset_dir=args.dataset_dir,
        repo_root=args.repo_root,
        export_onnx=args.onnx,
        input_height_px=args.input_height_px,
        input_width_px=args.input_width_px,
        max_val_samples=args.max_val_samples,
        manifests=args.manifest,
    )
    print(
        json.dumps(
            {
                "exported_files": manifest["exported_files"],
                "parity": manifest["parity"],
                "desktop_diagnostic_timings": manifest["desktop_diagnostic_timings"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
