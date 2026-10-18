"""Plate candidate proposers for Phase A (#233): baseline Hough, GroundingDINO (+ SAM 2.1), SAM 3.

Each proposer maps one decoded BGR uint8 frame to candidate circles with a method-specific score.
Scores are not comparable across methods and are not calibrated probabilities. Every candidate is
machine-origin. Models run through Hugging Face ``transformers`` from checkpoints the owner approved
on #233; weights load from the local Hugging Face cache, and the checkpoint revision and file hashes
go into the run provenance.
"""
from __future__ import annotations

import hashlib
import math
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

import geometry

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research" / "vbt-workflow"))

GDINO_CHECKPOINT = "IDEA-Research/grounding-dino-base"
SAM2_CHECKPOINT = "facebook/sam2.1-hiera-base-plus"
SAM3_CHECKPOINT = "facebook/sam3"
PROMPTS = ("weight plate", "barbell plate")
GDINO_BOX_THRESHOLD = 0.20  # low on purpose: Phase A measures recall, selection comes after
GDINO_TEXT_THRESHOLD = 0.20
SAM3_SCORE_THRESHOLD = 0.30
SAM3_MASK_THRESHOLD = 0.50
DUPLICATE_IOU = 0.7
MAX_CANDIDATES = 20


def _row(circle: geometry.Circle, score: float, **extra: Any) -> dict[str, Any]:
    return {"center_x_px": round(circle.center_x_px, 3), "center_y_px": round(circle.center_y_px, 3),
            "radius_px": round(circle.radius_px, 3), "score": round(float(score), 5), "fit": circle.fit,
            "fit_rms_px": None if circle.fit_rms_px is None else round(circle.fit_rms_px, 3),
            "arc_coverage": None if circle.arc_coverage is None else round(circle.arc_coverage, 4), **extra}


def box_iou(a: list[float], b: list[float]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def valid_box(box: list[float]) -> bool:
    """Finite with positive width and height. Boxes may extend past the frame: the detector
    predicts the full extent of a plate cut by the frame edge, which circle_from_box relies on."""
    return len(box) == 4 and all(math.isfinite(v) for v in box) and box[2] > box[0] and box[3] > box[1]


def dedupe(boxes: list[list[float]], scores: list[float]) -> list[int]:
    """Indices kept by greedy NMS across prompts (the two prompts often find the same plate);
    invalid boxes are dropped first."""
    order = sorted((i for i in range(len(boxes)) if valid_box(boxes[i]) and math.isfinite(scores[i])),
                   key=lambda i: (-scores[i], boxes[i]))
    kept: list[int] = []
    for i in order:
        if all(box_iou(boxes[i], boxes[k]) < DUPLICATE_IOU for k in kept):
            kept.append(i)
    return kept[:MAX_CANDIDATES]


class HoughProposer:
    """Unchanged ``plate-hough-edge-v1`` ranking as observed by ``hough_observe``; score = support."""
    name = "hough"

    def __init__(self) -> None:
        import hough_observe
        import vbt_suggest
        self._observe = hough_observe.observe_plate
        self._module = vbt_suggest

    def config(self) -> dict[str, Any]:
        return {"method": self._module.PLATE_METHOD, "parameters": asdict(self._module.PlateParams()),
                "source_sha256": hashlib.sha256(Path(self._module.__file__).read_bytes()).hexdigest()}

    def propose(self, image: np.ndarray) -> list[dict[str, Any]]:
        ranked = self._observe(image)["ranked"][:MAX_CANDIDATES]
        return [_row(geometry.Circle(r["center_x_px"], r["center_y_px"], r["radius_px"], "hough"),
                     r["support"], rank=r["rank"]) for r in ranked]


class _Torch:
    def __init__(self) -> None:
        import torch
        self.torch = torch
        self.device = "cuda" if torch.cuda.is_available() else "cpu"


class GroundingDinoProposer(_Torch):
    """Text-prompted boxes. ``with_sam2`` refines each box into a SAM 2.1 mask and fits the rim."""

    def __init__(self, with_sam2: bool) -> None:
        super().__init__()
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor
        self.name = "gdino_sam2" if with_sam2 else "gdino_box"
        self.processor = AutoProcessor.from_pretrained(GDINO_CHECKPOINT)
        self.model = AutoModelForZeroShotObjectDetection.from_pretrained(GDINO_CHECKPOINT).to(self.device).eval()
        self.sam = None
        if with_sam2:
            from transformers import Sam2Model, Sam2Processor
            self.sam_processor = Sam2Processor.from_pretrained(SAM2_CHECKPOINT)
            self.sam = Sam2Model.from_pretrained(SAM2_CHECKPOINT).to(self.device).eval()

    def config(self) -> dict[str, Any]:
        config = {"checkpoint": GDINO_CHECKPOINT, "prompts": list(PROMPTS),
                  "box_threshold": GDINO_BOX_THRESHOLD, "text_threshold": GDINO_TEXT_THRESHOLD,
                  "duplicate_iou": DUPLICATE_IOU, "max_candidates": MAX_CANDIDATES}
        if self.sam is not None:
            config["refiner"] = {"checkpoint": SAM2_CHECKPOINT, "multimask_output": False}
        return config

    def _boxes(self, pil: Any) -> list[tuple[list[float], float, str]]:
        text = [[f"{p}." for p in PROMPTS]]
        inputs = self.processor(images=pil, text=text, return_tensors="pt").to(self.device)
        with self.torch.no_grad():
            outputs = self.model(**inputs)
        result = self.processor.post_process_grounded_object_detection(
            outputs, inputs.input_ids, threshold=GDINO_BOX_THRESHOLD, text_threshold=GDINO_TEXT_THRESHOLD,
            target_sizes=[pil.size[::-1]])[0]
        labels = [str(label) for label in result.get("text_labels", result.get("labels", []))]
        boxes, scores = result["boxes"].tolist(), result["scores"].tolist()
        return [(boxes[i], scores[i], labels[i]) for i in dedupe(boxes, scores)]

    def _masks(self, pil: Any, boxes: list[list[float]]) -> list[np.ndarray]:
        inputs = self.sam_processor(images=pil, input_boxes=[boxes], return_tensors="pt").to(self.device)
        with self.torch.no_grad():
            outputs = self.sam(**inputs, multimask_output=False)
        masks = self.sam_processor.post_process_masks(outputs.pred_masks.cpu(), inputs["original_sizes"])[0]
        return [m.squeeze().numpy().astype(bool) for m in masks]

    def propose(self, image: np.ndarray) -> list[dict[str, Any]]:
        return self.propose_with_masks(image)[0]

    def propose_with_masks(self, image: np.ndarray) -> tuple[list[dict[str, Any]], list[np.ndarray | None]]:
        """Candidate rows and, aligned with them, the SAM 2.1 mask each circle was fitted to (None
        for box-only circles). Phase A.2 propagates the selected mask through the clip."""
        from PIL import Image
        pil = Image.fromarray(image[:, :, ::-1].copy())
        found = self._boxes(pil)
        if not found:
            return [], []
        masks = self._masks(pil, [box for box, _, _ in found]) if self.sam is not None else [None] * len(found)
        rows, used = [], []
        for (box, score, label), mask in zip(found, masks):
            circle = geometry.circle_from_mask(mask) if mask is not None else None
            if circle is None:
                circle = geometry.circle_from_box(*box)
            rows.append(_row(circle, score, prompt=label, box_xyxy=[round(v, 2) for v in box]))
            used.append(mask if circle.fit != "box" else None)
        return rows, used


class Sam3Proposer(_Torch):
    """Text-prompted instance masks from SAM 3, one pass per prompt, rim fitted to each mask."""
    name = "sam3"

    def __init__(self) -> None:
        super().__init__()
        from transformers import Sam3Model, Sam3Processor
        self.processor = Sam3Processor.from_pretrained(SAM3_CHECKPOINT)
        dtype = self.torch.bfloat16 if self.device == "cuda" else self.torch.float32
        self.model = Sam3Model.from_pretrained(SAM3_CHECKPOINT, dtype=dtype).to(self.device).eval()
        self.dtype = dtype

    def config(self) -> dict[str, Any]:
        return {"checkpoint": SAM3_CHECKPOINT, "prompts": list(PROMPTS), "dtype": str(self.dtype),
                "score_threshold": SAM3_SCORE_THRESHOLD, "mask_threshold": SAM3_MASK_THRESHOLD,
                "duplicate_iou": DUPLICATE_IOU, "max_candidates": MAX_CANDIDATES}

    def propose(self, image: np.ndarray) -> list[dict[str, Any]]:
        return self.propose_with_masks(image)[0]

    def propose_with_masks(self, image: np.ndarray) -> tuple[list[dict[str, Any]], list[np.ndarray | None]]:
        """Candidate rows and, aligned with them, the SAM 3 mask each circle was fitted to (None for
        box-only circles). The SAM 3 seed in Phase A.2 propagates the selected mask with SAM 2.1."""
        from PIL import Image
        pil = Image.fromarray(image[:, :, ::-1].copy())
        boxes, scores, labels, masks = [], [], [], []
        for prompt in PROMPTS:
            inputs = self.processor(images=pil, text=prompt, return_tensors="pt").to(self.device)
            inputs = {k: (v.to(self.dtype) if hasattr(v, "is_floating_point") and v.is_floating_point() else v)
                      for k, v in inputs.items()}
            with self.torch.no_grad():
                outputs = self.model(**inputs)
            result = self.processor.post_process_instance_segmentation(
                outputs, threshold=SAM3_SCORE_THRESHOLD, mask_threshold=SAM3_MASK_THRESHOLD,
                target_sizes=[[pil.size[1], pil.size[0]]])[0]
            for box, score, mask in zip(result["boxes"].tolist(), result["scores"].float().tolist(), result["masks"]):
                boxes.append(box)
                scores.append(score)
                labels.append(prompt)
                masks.append(mask.cpu().numpy().astype(bool))
        rows, used = [], []
        for i in dedupe(boxes, scores):
            circle = geometry.circle_from_mask(masks[i]) or geometry.circle_from_box(*boxes[i])
            rows.append(_row(circle, scores[i], prompt=labels[i], box_xyxy=[round(v, 2) for v in boxes[i]]))
            used.append(masks[i] if circle.fit != "box" else None)
        return rows, used


DFINE_CHECKPOINT = "ustc-community/dfine-small-coco"  # Apache-2.0, ~10M parameters (#233 Phase B)
DFINE_SCORE_THRESHOLD = 0.25


def synthetic_disc_mask(width_px: int, height_px: int, circle: geometry.Circle) -> np.ndarray:
    """Filled boolean disc mask from a fitted circle, for seeding a mask tracker from a box student."""
    yy, xx = np.mgrid[:height_px, :width_px]
    return (xx - circle.center_x_px) ** 2 + (yy - circle.center_y_px) ** 2 <= circle.radius_px ** 2


class DFineStudentProposer(_Torch):
    """Phase B distilled single-class plate detector (D-FINE-S, Apache-2.0, zero SAM 3 weights)."""
    name = "dfine_s_student"

    def __init__(
        self,
        checkpoint: str | Path = DFINE_CHECKPOINT,
        score_threshold: float = DFINE_SCORE_THRESHOLD,
        refine_rim: bool = True,
    ) -> None:
        super().__init__()
        from transformers import AutoImageProcessor, DFineForObjectDetection
        self.checkpoint = str(checkpoint)
        self.score_threshold = float(score_threshold)
        self.refine_rim = bool(refine_rim)
        self.processor = AutoImageProcessor.from_pretrained(self.checkpoint)
        self.model = DFineForObjectDetection.from_pretrained(self.checkpoint).to(self.device).eval()

    def config(self) -> dict[str, Any]:
        import rim_refine
        return {
            "checkpoint": self.checkpoint,
            "architecture": "DFineForObjectDetection",
            "license": "Apache-2.0",
            "score_threshold": self.score_threshold,
            "duplicate_iou": DUPLICATE_IOU,
            "max_candidates": MAX_CANDIDATES,
            "rim_refinement_rule": rim_refine.RULE if self.refine_rim else None,
        }

    def propose(self, image: np.ndarray) -> list[dict[str, Any]]:
        return self.propose_with_masks(image)[0]

    def propose_with_masks(self, image: np.ndarray) -> tuple[list[dict[str, Any]], list[np.ndarray | None]]:
        import rim_refine
        from PIL import Image
        pil = Image.fromarray(image[:, :, ::-1].copy())
        inputs = self.processor(images=pil, return_tensors="pt").to(self.device)
        with self.torch.no_grad():
            outputs = self.model(**inputs)
        result = self.processor.post_process_object_detection(
            outputs, threshold=self.score_threshold, target_sizes=[(pil.size[1], pil.size[0])]
        )[0]
        boxes = result["boxes"].float().tolist()
        scores = result["scores"].float().tolist()
        rows: list[dict[str, Any]] = []
        masks: list[np.ndarray | None] = []
        height_px, width_px = image.shape[:2]
        for i in dedupe(boxes, scores):
            raw_circle = geometry.circle_from_box(*boxes[i])
            raw_box_meta = {
                "center_x_px": round(raw_circle.center_x_px, 3),
                "center_y_px": round(raw_circle.center_y_px, 3),
                "radius_px": round(raw_circle.radius_px, 3),
                "fit": raw_circle.fit,
            }
            if self.refine_rim:
                ref = rim_refine.refine_circle(image, raw_circle)
                circle = ref.refined
                ref_meta: dict[str, Any] | None = {
                    "rule": rim_refine.RULE,
                    "used_refined": ref.used_refined,
                    "reason": ref.reason,
                    "inlier_count": ref.inlier_count,
                }
            else:
                circle = raw_circle
                ref_meta = None
            rows.append(
                _row(
                    circle,
                    scores[i],
                    box_xyxy=[round(v, 2) for v in boxes[i]],
                    mask_source="synthetic_disc_from_box",
                    raw_box_circle=raw_box_meta,
                    rim_refinement=ref_meta,
                )
            )
            masks.append(synthetic_disc_mask(width_px, height_px, circle))
        return rows, masks


def build(name: str, checkpoint: str | Path | None = None) -> Any:
    if name == "hough":
        return HoughProposer()
    if name in ("gdino_box", "gdino_sam2"):
        return GroundingDinoProposer(with_sam2=name == "gdino_sam2")
    if name == "sam3":
        return Sam3Proposer()
    if name == "dfine_s_student":
        return DFineStudentProposer(checkpoint=checkpoint or DFINE_CHECKPOINT)
    raise ValueError(f"unknown method {name!r}")
