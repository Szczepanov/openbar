"""Development clip allow-list and frame plan for Phase A (#233). Stdlib only.

Allowed: ``split-exploration`` clips, and the 13 clips scored once by the frozen baseline run
(PLATE_SELECTION_BASELINE_REPORT.md), which PLATE_SELECTION_NEXT_ROUND_PLAN.md and rule 3 of the
shared-pool amendment (PLATE_SELECTION_SPLIT.md) made development-only for #178 and #57.
Everything else is refused, ``split-reserved-57`` first and unconditionally. The unexposed pilot and
refusal-candidate clips stay unseen so that a later frozen split can still use them.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "validation" / "tools"))
import held_out_guard  # noqa: E402

EXPLORATION_TAG = "split-exploration"
SCORED_DEVELOPMENT_ONLY = frozenset({
    "self-back-squat-side-1015-06", "self-back-squat-side-1015-09", "self-back-squat-side-1015-10",
    "self-clean-side-0203-17", "self-other-side-0215-27", "self-snatch-side-0206-19",
    "self-snatch-side-0212-24", "self-snatch-side-0213-25", "self-snatch-side-1015-01",
    "self-snatch-side-1015-02", "self-snatch-side-1015-03", "self-snatch-side-1015-04",
    "self-snatch-side-1015-05",
})
GRID_STEP_S = 0.5
MAX_GRID_FRAMES = 40
TRACK_STRIDE = 2  # Phase A.2: every 2nd decoded frame (about 30 fps on 60 fps clips, 15 fps on 30 fps clips)


class ClipRefused(RuntimeError):
    pass


def tags(fixture: dict[str, Any]) -> list[str]:
    return list((fixture.get("conditions") or {}).get("challenge_tags") or [])


def development_role(fixture: dict[str, Any]) -> str:
    """``exploration`` or ``scored-development-only``; raises ClipRefused for anything else."""
    name = fixture.get("id", "<no id>")
    if held_out_guard.RESERVED_TAG in tags(fixture):
        raise ClipRefused(f"{name} is {held_out_guard.RESERVED_TAG}; never opened")
    if EXPLORATION_TAG in tags(fixture):
        if held_out_guard.is_held_out(fixture):
            raise ClipRefused(f"{name} is tagged exploration but held-out; refusing the conflict")
        return "exploration"
    if name in SCORED_DEVELOPMENT_ONLY:
        return "scored-development-only"
    raise ClipRefused(f"{name} is not a Phase A development clip")


def load_fixtures(manifests: list[Path]) -> dict[str, dict[str, Any]]:
    fixtures: dict[str, dict[str, Any]] = {}
    for manifest in manifests:
        for fixture in json.loads(manifest.read_text(encoding="utf-8"))["fixtures"]:
            if fixture["id"] in fixtures:
                raise ValueError(f"duplicate fixture id {fixture['id']}")
            fixtures[fixture["id"]] = fixture
    return fixtures


def development_clips(fixtures: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Allowed clip id -> role, in id order; refused clips are simply absent."""
    allowed = {}
    for name in sorted(fixtures):
        try:
            allowed[name] = development_role(fixtures[name])
        except ClipRefused:
            continue
    return allowed


def _nearest(timestamps_s: list[float]):
    from bisect import bisect_left

    def nearest(t: float) -> int:
        right = bisect_left(timestamps_s, t)
        options = [i for i in (right - 1, right) if 0 <= i < len(timestamps_s)]
        return min(options, key=lambda i: (abs(timestamps_s[i] - t), i))
    return nearest


def frame_plan(timestamps_s: list[float], truth_times_s: list[float]) -> list[int]:
    """Decoded frame indices to run: the frame nearest each labelled time, plus a uniform grid every
    GRID_STEP_S (thinned to MAX_GRID_FRAMES evenly spaced), sorted and unique."""
    if not timestamps_s:
        raise ValueError("no decoded frames")
    nearest = _nearest(timestamps_s)
    start, end = timestamps_s[0], timestamps_s[-1]
    grid_times = [start + k * GRID_STEP_S for k in range(int((end - start) / GRID_STEP_S) + 1)]
    if len(grid_times) > MAX_GRID_FRAMES:
        step = (len(grid_times) - 1) / (MAX_GRID_FRAMES - 1)
        grid_times = [grid_times[round(k * step)] for k in range(MAX_GRID_FRAMES)]
    return sorted({nearest(t) for t in [*grid_times, *truth_times_s]})


def track_plan(timestamps_s: list[float], truth_times_s: list[float], stride: int = TRACK_STRIDE) -> list[int]:
    """Decoded frame indices a tracker runs on, in order: every ``stride``-th frame from frame 0
    (the label-free decision frame), plus the frame nearest each labelled time so every label is
    scorable. Labelled times add frames only; they never choose the decision frame."""
    if not timestamps_s:
        raise ValueError("no decoded frames")
    if stride < 1:
        raise ValueError("stride must be at least 1")
    nearest = _nearest(timestamps_s)
    return sorted({*range(0, len(timestamps_s), stride), *(nearest(t) for t in truth_times_s)})
