"""Label-free selection rules and the descriptive identity match used by the scorer (#233).

Selection never reads labels. ``match`` is scoring only. Rules: ``top1`` (highest score),
``plausible`` + ``top1``, ``largest-plausible-v1`` and ``motion-v1``, plus the ``close-score-v1``
refusal (Phase A.2).

``motion-v1`` is designed against H1/H2 (PLATE_SELECTION_NEXT_ROUND_PLAN.md): clutter and stored
plates stay put for the whole clip, and the plate on the lifted bar leaves its starting spot. For
each plausible candidate on the decision frame it measures *persistence*: the share of the clip's
other sampled frames where the same method reports a similar circle at the same spot. The least
persistent plausible candidate wins.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

MATCH_RULE = "identity-loose-v1"
MATCH_CENTRE_FRACTION = 0.25  # of the true radius
MATCH_RADIUS_FRACTION = 0.25

# Plausible plate size: the baseline's own PlateParams radius window (vbt_suggest.py), reused
# unchanged so no new constant is tuned on development clips.
MIN_RADIUS_FRACTION = 0.04  # of the shorter frame side
MAX_RADIUS_FRACTION = 0.30
LARGEST_RULE = "largest-plausible-v1"

MOTION_RULE = "motion-v1"
PLAUSIBLE_SCORE_FRACTION = 0.5  # of the best score on the decision frame
SAME_SPOT_CENTRE_FRACTION = 0.25  # of the candidate radius
SAME_SPOT_RADIUS_FRACTION = 0.25
MIN_OTHER_FRAMES = 4

# Refusal (Phase A.2): fixed before any A.2 result was produced, but after reading the Phase A failure
# review, which reports one stored plate scoring 0.43 against 0.41 for the target. A ratio needs no
# common score scale, but it assumes the method's scores spread out between objects. GroundingDINO's do;
# SAM 3 gives several plates scores above 0.9, so the same ratio refuses 11 of its 27 frame-0 decisions.
REFUSAL_RULE = "close-score-v1"
REFUSAL_SCORE_RATIO = 0.9  # refuse when the runner-up scores at least this share of the best

# SAM 3 seed (owner chose SAM 3 as the lead candidate on 2026-10-17). A development estimate, not a
# held-out test. What was known when these rules were fixed:
# - in-frame-v1 comes straight from a known A.2 failure: on self-other-side-0215-27 the seed was a
#   plate centred off the left edge (x about -23 px), and the A.2 results suggest this check.
# - close-score-v1 would refuse 11 of 27 SAM 3 frame-0 decisions, and on self-back-squat-side-1015-06
#   the plate on the bar scores 0.95 against 0.87 for a stored plate (gap 0.08). MARGIN_SCORE = 0.05
#   sits below that one known gap, so that known-correct choice stays accepted.
# - The Phase A SAM 3 run already holds frame-0 candidates, so this rule's frame-0 outcome could have
#   been computed from existing data; nobody looked at that margin distribution before fixing it.
#   The one-clip smoke run of the first version showed a margin of 0.21 on one vbt clip.
# SAM 3 scores saturate near 1 for clear plates, so a ratio says little; an absolute margin does.
# A runner-up centred on the best candidate (within SAME_SPOT_CENTRE_FRACTION of its radius, any
# radius) is a rim, hub, duplicate mask or another plate on the same bar end: choosing it would give
# the same centre path, so it is not a rival. This protects identity, not scale: if an inner rim or hub
# outscores the outer rim, it becomes the seed unchallenged and its smaller radius is what gets tracked.
# Margins are rounded to the 5 decimals scores are stored with before comparing, so the boundary does
# not depend on float error.
IN_FRAME_RULE = "in-frame-v1"
MARGIN_RULE = "sam3-margin-v2"
MARGIN_SCORE = 0.05  # refuse when a plate centred elsewhere scores less than this below the best
SCORE_DECIMALS = 5


def in_frame(candidates: Sequence[Candidate], width_px: int, height_px: int) -> list[Candidate]:
    """Candidates whose centre lies in the frame (pixel centres, 0 <= x < width). A plate centred
    off the edge cannot be the measured plate: its rim, and so the scale, is mostly unseen."""
    return [c for c in candidates if 0 <= c.center_x_px < width_px and 0 <= c.center_y_px < height_px]


def concentric(best: Candidate, other: Candidate) -> bool:
    """Centred on ``best`` within ``SAME_SPOT_CENTRE_FRACTION`` of its radius, whatever the radius."""
    return (math.hypot(best.center_x_px - other.center_x_px, best.center_y_px - other.center_y_px)
            <= SAME_SPOT_CENTRE_FRACTION * best.radius_px)


def margin_refusal(candidates: Sequence[Candidate]) -> dict:
    """``sam3-margin-v2`` on already-filtered candidates: refuse when a candidate not concentric with
    the best scores less than ``MARGIN_SCORE`` below it. Same reporting shape as ``refusal``."""
    ordered = _order(candidates)
    if not ordered:
        return {"rule": MARGIN_RULE, "refused": False, "reason": "no_candidate", "margin": None}
    rivals = [c for c in ordered[1:] if not concentric(ordered[0], c)]
    if not rivals:
        return {"rule": MARGIN_RULE, "refused": False, "reason": None, "margin": None}
    margin = round(ordered[0].score - rivals[0].score, SCORE_DECIMALS)
    refused = margin < MARGIN_SCORE
    return {"rule": MARGIN_RULE, "refused": refused, "reason": "close_scores" if refused else None,
            "margin": margin}


@dataclass(frozen=True)
class Candidate:
    center_x_px: float
    center_y_px: float
    radius_px: float
    score: float

    @staticmethod
    def from_dict(row: dict) -> "Candidate":
        return Candidate(float(row["center_x_px"]), float(row["center_y_px"]),
                         float(row["radius_px"]), float(row["score"]))


def match(candidate: Candidate, x: float, y: float, r: float) -> bool:
    """Same physical plate, loosely: centre within 25% and radius within 25% of the true radius."""
    return (math.hypot(candidate.center_x_px - x, candidate.center_y_px - y) <= MATCH_CENTRE_FRACTION * r
            and abs(candidate.radius_px - r) <= MATCH_RADIUS_FRACTION * r)


def _order(candidates: Iterable[Candidate]) -> list[Candidate]:
    """Deterministic order: score descending, then geometry, so ties never depend on input order."""
    return sorted(candidates, key=lambda c: (-c.score, c.center_x_px, c.center_y_px, c.radius_px))


def select_top1(candidates: Sequence[Candidate]) -> Candidate | None:
    ordered = _order(candidates)
    return ordered[0] if ordered else None


def plausible(candidates: Sequence[Candidate], width_px: int, height_px: int) -> list[Candidate]:
    """Candidates whose radius fits the baseline's plate-size window for this frame.

    Open-vocabulary detectors also box the whole loaded barbell or the lifter for "barbell plate";
    those circles are far larger than any plate that fits the frame.
    """
    shorter = min(width_px, height_px)
    low, high = MIN_RADIUS_FRACTION * shorter, MAX_RADIUS_FRACTION * shorter
    return [c for c in candidates if low <= c.radius_px <= high]


def select_largest(candidates: Sequence[Candidate]) -> Candidate | None:
    """Largest candidate among those scoring at least PLAUSIBLE_SCORE_FRACTION of the best.

    In a side view the plate on the lifted bar is usually nearer the camera than stored plates.
    """
    ordered = _order(candidates)
    if not ordered:
        return None
    floor = PLAUSIBLE_SCORE_FRACTION * ordered[0].score
    return min((c for c in ordered if c.score >= floor), key=lambda c: (-c.radius_px, -c.score, c.center_x_px))


def same_spot(a: Candidate, b: Candidate) -> bool:
    return (math.hypot(a.center_x_px - b.center_x_px, a.center_y_px - b.center_y_px)
            <= SAME_SPOT_CENTRE_FRACTION * a.radius_px
            and abs(a.radius_px - b.radius_px) <= SAME_SPOT_RADIUS_FRACTION * a.radius_px)


def persistence(candidate: Candidate, other_frames: Sequence[Sequence[Candidate]]) -> float:
    if not other_frames:
        raise ValueError("persistence needs at least one other frame")
    hits = sum(1 for frame in other_frames if any(same_spot(candidate, other) for other in frame))
    return hits / len(other_frames)


def select_motion(decision: Sequence[Candidate], other_frames: Sequence[Sequence[Candidate]]
                  ) -> tuple[Candidate | None, dict]:
    """Least persistent plausible candidate; falls back to top-1 with too few other frames."""
    ordered = _order(decision)
    if not ordered:
        return None, {"rule": MOTION_RULE, "fallback": None}
    if len(other_frames) < MIN_OTHER_FRAMES:
        return ordered[0], {"rule": MOTION_RULE, "fallback": "top1_too_few_frames"}
    floor = PLAUSIBLE_SCORE_FRACTION * ordered[0].score
    plausible = [c for c in ordered if c.score >= floor]
    scored = [(persistence(c, other_frames), index, c) for index, c in enumerate(plausible)]
    best_p, _, best = min(scored, key=lambda item: (item[0], item[1]))
    return best, {"rule": MOTION_RULE, "fallback": None, "plausible": len(plausible),
                  "persistence": [round(p, 4) for p, _, _ in scored], "selected_persistence": round(best_p, 4)}


def refusal(candidates: Sequence[Candidate]) -> dict:
    """``close-score-v1`` on already-filtered (plausible) candidates: refuse when the two best are
    too close in score to name one plate without a human. One candidate or none never refuses here;
    no candidate is reported as ``no_candidate``."""
    ordered = _order(candidates)
    if not ordered:
        return {"rule": REFUSAL_RULE, "refused": False, "reason": "no_candidate", "ratio": None}
    if len(ordered) == 1:
        return {"rule": REFUSAL_RULE, "refused": False, "reason": None, "ratio": None}
    best, second = ordered[0].score, ordered[1].score
    ratio = second / best if best > 0 else 1.0
    refused = ratio >= REFUSAL_SCORE_RATIO
    return {"rule": REFUSAL_RULE, "refused": refused, "reason": "close_scores" if refused else None,
            "ratio": round(ratio, 4)}
