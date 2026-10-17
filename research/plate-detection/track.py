"""Phase A.2 (#233): detect once on the label-free decision frame, then track with SAM 2.1 video.

Seed: GroundingDINO + SAM 2.1 candidates on frame 0, the size window (``selection.plausible``) and
top-1. The seed's SAM 2.1 image mask prompts ``Sam2VideoModel`` in streaming mode, and each tracked
mask gets the same rim fit as Phase A (``geometry.circle_from_mask``). Each frame then carries at
most one candidate, the tracked plate; an empty mask is a lost frame with no candidate (no
coordinate is invented). The tracked candidate's score is the sigmoid of SAM 2.1's object-score
logit, which is specific to this tracker and not a calibrated probability.

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
SAM3_SEED_RULE = "sam3-seed-v1"
SAM2_VIDEO_CHECKPOINT = "facebook/sam2.1-hiera-base-plus"
OBJECT_ID = 1


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
    """``sam3-seed-v1``: like ``choose_seed``, but candidates must also be centred in the frame
    (``in-frame-v1``) and the refusal is ``sam3-margin-v1``. ``close-score-v1`` is recorded beside it
    for comparison only. No labels."""
    candidates = [selection.Candidate.from_dict(row) for row in rows]
    kept = selection.in_frame(selection.plausible(candidates, width_px, height_px), width_px, height_px)
    plausible_indices = [i for i, c in enumerate(candidates) if c in kept]
    top = selection.select_top1(kept)
    selected = None if top is None else next(i for i in plausible_indices if candidates[i] == top)
    return {"rule": SAM3_SEED_RULE, "selected_index": selected, "plausible_indices": plausible_indices,
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
    """One SAM 2.1 video session per clip, fed frame by frame (streaming).

    Session state older than the model's memory window is dropped after each frame: the model reads
    only the last ``num_maskmem`` non-conditioning outputs and ``max_object_pointers_in_encoder``
    object pointers, and the decoded-frame count it sees never falls below that window, so output is
    unchanged while memory stays bounded on long clips.
    """

    def __init__(self) -> None:
        import torch
        from transformers import Sam2VideoModel, Sam2VideoProcessor
        self.torch = torch
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.processor = Sam2VideoProcessor.from_pretrained(SAM2_VIDEO_CHECKPOINT)
        self.model = Sam2VideoModel.from_pretrained(SAM2_VIDEO_CHECKPOINT).to(self.device).eval()
        config = self.model.config
        self.window = max(config.num_maskmem, config.max_object_pointers_in_encoder) + 1
        self.session = None
        self.position = 0

    def config(self) -> dict[str, Any]:
        return {"checkpoint": SAM2_VIDEO_CHECKPOINT, "mode": "streaming", "prompt": "seed_mask",
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
