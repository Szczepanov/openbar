"""Phase A.2 (#233): detect once on the label-free decision frame, then track with SAM 2.1 video.

Seed: GroundingDINO + SAM 2.1 candidates on frame 0, the size window (``selection.plausible``) and
top-1. The seed's SAM 2.1 image mask prompts ``Sam2VideoModel`` in streaming mode, and each tracked
mask gets the same rim fit as Phase A (``geometry.circle_from_mask``). Each frame then carries at
most one candidate, the tracked plate; an empty mask is a lost frame with no candidate (no
coordinate is invented). The tracked candidate's score is the sigmoid of SAM 2.1's object-score
logit, which is specific to this tracker and not a calibrated probability.

The tracker is either SAM 2.1 (``sam2``) or EdgeTAM (``edgetam``, #57), Meta's on-device SAM 2
variant (Apache-2.0), loaded from the transformers-format conversion pinned in ``TRACKERS``. Both
use ``Sam2VideoProcessor`` and the same streaming loop, so only the checkpoint differs.

``choose_seed`` and ``tracked_row`` are pure (numpy only, via ``geometry``) and unit tested; the
model wrapper imports torch lazily.
"""
from __future__ import annotations

import math
from typing import Any, Sequence

import numpy as np

import geometry
import selection

METHOD = "track_sam2"
SAM3_METHOD = "track_sam2_sam3seed"
SAM3_SEED_RULE = "sam3-seed-v2"
STUDENT_SEED_RULE = "dfine-student-seed-v1"
SAM2_VIDEO_CHECKPOINT = "facebook/sam2.1-hiera-base-plus"
EDGETAM_VIDEO_CHECKPOINT = "yonigozlan/EdgeTAM-hf"  # transformers conversion of facebook/EdgeTAM, Apache-2.0
# Video trackers: checkpoint, transformers model class and pinned revision (None: whatever is cached,
# as Phase A.2 ran; the revision actually used is recorded in run.json either way).
TRACKERS = {
    "sam2": {"checkpoint": SAM2_VIDEO_CHECKPOINT, "model_class": "Sam2VideoModel", "revision": None},
    "edgetam": {"checkpoint": EDGETAM_VIDEO_CHECKPOINT, "model_class": "EdgeTamVideoModel",
                "revision": "c266ce53b3fc00f0f495b583f6a116c4e57f53bb"},
}
OBJECT_ID = 1
SEED_NAMES = ("gdino_sam2", "sam3", "dfine_s_student")
SEED_SUFFIXES = {
    "gdino_sam2": "",
    "sam3": "_sam3seed",
    "dfine_s_student": "_dfinestudentseed",
}

# Re-detection (#57), fixed before its run. When track-guard-v2 stops a track, SAM 3 runs again at
# most every REDETECT_INTERVAL_S of media; the sam3-seed-v2 filters apply, the plate must be within
# REDETECT_RADIUS_FRACTION of the seed's radius (side view: the plate's apparent size barely changes),
# and sam3-margin-v2 refuses when another plate scores close. Chosen after seeing that the guard stops
# correct tracks during fast pulls and that EdgeTAM re-locked onto an object of similar radius on
# self-clean-side-0203-17, which this radius check alone would not reject.
REDETECT_RULE = "redetect-v1"
REDETECT_INTERVAL_S = 0.5
REDETECT_RADIUS_FRACTION = 0.15


def method_name(tracker: str, seed: str, redetect: bool = False) -> str:
    """Output method for a tracker and seed; the two Phase A.2 names are kept unchanged."""
    if tracker not in TRACKERS:
        raise ValueError(f"unknown tracker {tracker!r}")
    if seed not in SEED_NAMES:
        raise ValueError(f"unknown seed {seed!r}; a new seed needs its own method suffix")
    if redetect and seed not in ("sam3", "dfine_s_student"):
        raise ValueError("re-detection is defined for SAM 3 or Phase B student seeds only")
    return f"track_{tracker}{SEED_SUFFIXES[seed]}" + ("_redetect" if redetect else "")


def _has_trackable_mask(row: dict[str, Any]) -> bool:
    return row.get("fit") != "box" or row.get("mask_source") == "synthetic_disc_from_box"


def choose_redetect(rows: Sequence[dict[str, Any]], width_px: int, height_px: int,
                    seed_radius_px: float) -> dict[str, Any]:
    """``redetect-v1``: the SAM 3 or Phase B student candidate to resume from after the guard stopped
    a track, or none. No labels."""
    candidates = [selection.Candidate.from_dict(row) for row in rows]
    window = selection.in_frame(selection.plausible(candidates, width_px, height_px), width_px, height_px)
    plausible_indices = [i for i, c in enumerate(candidates) if c in window and _has_trackable_mask(rows[i])]
    kept_indices = [i for i in plausible_indices
                    if abs(float(rows[i]["radius_px"]) - seed_radius_px) <= REDETECT_RADIUS_FRACTION * seed_radius_px]
    kept = [candidates[i] for i in kept_indices]
    refusal = selection.margin_refusal(kept)
    top = selection.select_top1(kept)
    selected = None
    if top is not None and not refusal["refused"]:
        selected = next(i for i in kept_indices if candidates[i] == top)
    return {"rule": REDETECT_RULE, "selected_index": selected, "matching_indices": kept_indices, "refusal": refusal}


def choose_seed(rows: Sequence[dict[str, Any]], width_px: int, height_px: int) -> dict[str, Any]:
    """Index of the plausible top-1 row on the decision frame (None when nothing is plausible), the
    plausible row indices and the ``close-score-v1`` refusal over the plausible rows. No labels."""
    candidates = [selection.Candidate.from_dict(row) for row in rows]
    kept = selection.plausible(candidates, width_px, height_px)
    plausible_indices = [i for i, c in enumerate(candidates) if c in kept]
    top = selection.select_top1(kept)
    selected = None if top is None else next(i for i in plausible_indices if candidates[i] == top)
    return {"selected_index": selected, "plausible_indices": plausible_indices,
            "refusal": selection.refusal(kept)}


def choose_seed_sam3(rows: Sequence[dict[str, Any]], width_px: int, height_px: int) -> dict[str, Any]:
    """``sam3-seed-v2``: like ``choose_seed``, but candidates must also be centred in the frame
    (``in-frame-v1``) and have a mask the tracker can start from (not a box-only fit), and the
    refusal is ``sam3-margin-v2``. ``close-score-v1`` is recorded beside it for comparison only.
    No labels."""
    candidates = [selection.Candidate.from_dict(row) for row in rows]
    window = selection.in_frame(selection.plausible(candidates, width_px, height_px), width_px, height_px)
    plausible_indices = [i for i, c in enumerate(candidates) if c in window and rows[i].get("fit") != "box"]
    kept = [candidates[i] for i in plausible_indices]
    top = selection.select_top1(kept)
    selected = None if top is None else next(i for i in plausible_indices if candidates[i] == top)
    return {"rule": SAM3_SEED_RULE, "selected_index": selected, "plausible_indices": plausible_indices,
            "refusal": selection.margin_refusal(kept), "close_score_refusal": selection.refusal(kept)}


def choose_seed_student(rows: Sequence[dict[str, Any]], width_px: int, height_px: int) -> dict[str, Any]:
    """``dfine-student-seed-v1``: Phase B student seed rule (size window, ``in-frame-v1``, synthetic
    disc mask from box, and ``sam3-margin-v2`` refusal). No labels."""
    candidates = [selection.Candidate.from_dict(row) for row in rows]
    window = selection.in_frame(selection.plausible(candidates, width_px, height_px), width_px, height_px)
    plausible_indices = [i for i, c in enumerate(candidates) if c in window and _has_trackable_mask(rows[i])]
    kept = [candidates[i] for i in plausible_indices]
    top = selection.select_top1(kept)
    selected = None if top is None else next(i for i in plausible_indices if candidates[i] == top)
    return {"rule": STUDENT_SEED_RULE, "selected_index": selected, "plausible_indices": plausible_indices,
            "refusal": selection.margin_refusal(kept), "close_score_refusal": selection.refusal(kept)}


def tracked_row(circle: geometry.Circle | None, object_score_logit: float) -> dict[str, Any] | None:
    """Candidate row for one tracked mask, in the Phase A row format; None for an empty mask."""
    if circle is None:
        return None
    if not math.isfinite(object_score_logit):
        raise ValueError(f"non-finite object score logit {object_score_logit!r}")
    return {"center_x_px": round(circle.center_x_px, 3), "center_y_px": round(circle.center_y_px, 3),
            "radius_px": round(circle.radius_px, 3),
            "score": round(1.0 / (1.0 + math.exp(-object_score_logit)), 5), "fit": circle.fit,
            "fit_rms_px": None if circle.fit_rms_px is None else round(circle.fit_rms_px, 3),
            "arc_coverage": None if circle.arc_coverage is None else round(circle.arc_coverage, 4),
            "object_score_logit": round(object_score_logit, 4)}


class Sam2VideoTracker:
    """One SAM 2.1 or EdgeTAM video session per clip, fed frame by frame (streaming).

    Session state older than the model's memory window is dropped after each frame: the model reads
    only the last ``num_maskmem`` non-conditioning outputs and ``max_object_pointers_in_encoder``
    object pointers, and the decoded-frame count it sees never falls below that window, so output is
    unchanged while memory stays bounded on long clips.
    """

    def __init__(self, name: str = "sam2") -> None:
        import torch
        import transformers
        spec = TRACKERS[name]
        self.name, self.spec = name, spec
        self.torch = torch
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        model_class = getattr(transformers, spec["model_class"])
        self.processor = transformers.Sam2VideoProcessor.from_pretrained(spec["checkpoint"], revision=spec["revision"])
        self.model = model_class.from_pretrained(spec["checkpoint"], revision=spec["revision"]).to(self.device).eval()
        config = self.model.config
        self.window = max(config.num_maskmem, config.max_object_pointers_in_encoder) + 1
        self.session = None
        self.position = 0

    def config(self) -> dict[str, Any]:
        return {"tracker": self.name, "checkpoint": self.spec["checkpoint"], "model_class": self.spec["model_class"],
                "mode": "streaming", "prompt": "seed_mask",
                "dtype": "float32", "video_storage_device": "cpu", "state_window_frames": self.window}

    def start(self, seed_mask: np.ndarray) -> None:
        self.session = self.processor.init_video_session(inference_device=self.device, video_storage_device="cpu",
                                                         dtype=self.torch.float32)
        self.position = 0
        self._seed = seed_mask

    def step(self, image: np.ndarray) -> tuple[np.ndarray, float]:
        """Mask (bool, frame size) and object-score logit for the next frame in clip order."""
        from PIL import Image
        if self.session is None:
            raise RuntimeError("start() a session first")
        pil = Image.fromarray(image[:, :, ::-1].copy())
        inputs = self.processor(images=pil, device=self.device, return_tensors="pt")
        if self.position == 0:
            self.processor.add_inputs_to_inference_session(
                inference_session=self.session, frame_idx=0, obj_ids=OBJECT_ID,
                input_masks=self.torch.from_numpy(self._seed))
        with self.torch.no_grad():
            output = self.model(inference_session=self.session, frame=inputs.pixel_values[0].to(self.device),
                                frame_idx=self.position)
        masks = self.processor.post_process_masks([output.pred_masks], original_sizes=inputs.original_sizes,
                                                  binarize=True)[0]
        mask = masks[0, 0].cpu().numpy().astype(bool)
        logit = float(output.object_score_logits.reshape(-1)[0].item())
        self._prune()
        self.position += 1
        return mask, logit

    def _prune(self) -> None:
        oldest = self.position - self.window
        frames = self.session.processed_frames
        for key in [k for k in frames if k <= oldest]:
            del frames[key]
        for outputs in self.session.output_dict_per_obj.values():
            stale = outputs["non_cond_frame_outputs"]
            for key in [k for k in stale if k <= oldest]:
                del stale[key]
