"""Voter disagreement, labelling triage and calibration (research only, standard library only).

Several independent plate-centre methods ("voters") are compared against each other. Where they
disagree, or where any of them lost the plate, a human labels that frame. A seeded random sample of
all frames is labelled as well, so that the rate at which agreement is wrong can be measured without
selection bias (`calibrate`).

Nothing here is ground truth. Agreement is evidence about the voters, never a label. Rules kept from
AGENTS.md: a lost or missing voter observation carries no coordinate and is never interpolated;
NaN and infinities are rejected; output is deterministic (no clock, no unseeded randomness).
"""
from __future__ import annotations

import math
import random
from bisect import bisect_left
from itertools import combinations
from typing import Any, Mapping, Sequence

Point = tuple[float, float]
Observation = Point | None

# Fixed before any data is seen: the original #57 plate-centre MAE gate. Never derive these from
# scored data (docs/validation/TRACKER_FILTER_SELECTION.md).
SPREAD_LIMIT_PX = 3.0
GATE_PX = 3.0
TIMESTAMP_TOLERANCE_S = 0.0005
WILSON_Z = 1.959964


class TriageError(ValueError):
    """Invalid input; the caller reports it and nothing is written."""


def _finite(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise TriageError(f"{what} must be a finite number, got {value!r}")
    return float(value)


def _point(raw: Any, what: str) -> Point:
    if not isinstance(raw, Mapping):
        raise TriageError(f"{what} must be an object with x_px and y_px")
    return (_finite(raw.get("x_px"), f"{what}.x_px"), _finite(raw.get("y_px"), f"{what}.y_px"))


def load_prediction_centres(document: Mapping[str, Any]) -> dict[float, Observation]:
    """tracker-prediction-v1 samples as timestamp -> centre; a lost sample maps to None."""
    if document.get("schema_version") != 1 or not isinstance(document.get("samples"), list):
        raise TriageError("not a tracker-prediction-v1 document")
    centres: dict[float, Observation] = {}
    previous = -math.inf
    for index, sample in enumerate(document["samples"]):
        stamp = _finite(sample.get("timestamp_s"), f"samples[{index}].timestamp_s")
        if stamp <= previous:
            raise TriageError(f"samples[{index}].timestamp_s is not strictly increasing")
        previous = stamp
        state = sample.get("state")
        if state == "tracked":
            centres[stamp] = _point(sample.get("center_px"), f"samples[{index}].center_px")
        elif state == "lost":
            if "center_px" in sample:
                raise TriageError(f"samples[{index}] is lost but carries a coordinate")
            centres[stamp] = None
        else:
            raise TriageError(f"samples[{index}].state must be tracked or lost, got {state!r}")
    return centres


def load_label_centres(document: Mapping[str, Any]) -> dict[float, Point]:
    """annotation-v1 samples that carry a human plate centre, as timestamp -> centre."""
    if document.get("schema_version") != 1 or not isinstance(document.get("samples"), list):
        raise TriageError("not an annotation-v1 document")
    labels: dict[float, Point] = {}
    for index, sample in enumerate(document["samples"]):
        if sample.get("annotation_state") != "labelled":
            continue
        stamp = _finite(sample.get("timestamp_s"), f"samples[{index}].timestamp_s")
        labels[stamp] = _point(sample.get("center_px"), f"samples[{index}].center_px")
    return labels


def lookup(centres: Mapping[float, Observation], stamps: Sequence[float], stamp: float,
           tolerance_s: float = TIMESTAMP_TOLERANCE_S) -> tuple[bool, Observation]:
    """(present, centre): present is False when no sample lies within the tolerance of `stamp`."""
    at = bisect_left(stamps, stamp)
    best = None
    for candidate in stamps[max(0, at - 1):at + 1]:
        if abs(candidate - stamp) <= tolerance_s and (best is None or abs(candidate - stamp) < abs(best - stamp)):
            best = candidate
    return (False, None) if best is None else (True, centres[best])


def _distance(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def compare_voters(voters: Mapping[str, Mapping[float, Observation]], timestamps: Sequence[float],
                   spread_limit_px: float = SPREAD_LIMIT_PX) -> list[dict[str, Any]]:
    """One row per requested timestamp: voter centres, spread, median and the reasons it is flagged.

    A frame is flagged when any voter is lost or has no sample there, or when the largest pairwise
    distance between voters exceeds `spread_limit_px`. The median is component-wise over the voters
    that have a coordinate and exists only when at least two do.
    """
    names = sorted(voters)
    if len(names) < 3:
        raise TriageError("at least three voters are required")
    sorted_stamps = {name: sorted(voters[name]) for name in names}
    rows: list[dict[str, Any]] = []
    for stamp in timestamps:
        positions: dict[str, Observation] = {}
        reasons: list[str] = []
        for name in names:
            present, centre = lookup(voters[name], sorted_stamps[name], stamp)
            positions[name] = centre
            if not present:
                reasons.append(f"missing:{name}")
            elif centre is None:
                reasons.append(f"lost:{name}")
        valid = [centre for centre in positions.values() if centre is not None]
        spread = max((_distance(a, b) for a, b in combinations(valid, 2)), default=None)
        median = (None if len(valid) < 2 else
                  (_median([p[0] for p in valid]), _median([p[1] for p in valid])))
        if spread is not None and spread > spread_limit_px:
            reasons.append("spread")
        rows.append({"timestamp_s": stamp, "positions": positions, "n_valid": len(valid),
                     "spread_px": spread, "median_px": median, "flags": reasons})
    return rows


def select_frames(timestamps: Sequence[float], rows: Sequence[Mapping[str, Any]], fixture_id: str,
                  fraction: float, seed: int) -> list[dict[str, Any]]:
    """Frames a human should label: the seed frame, every flagged frame and a seeded random sample.

    The sample is drawn from all frames, flagged or not, so the sampled subset is an unbiased
    picture of the clip. Each result says why it was chosen (kept apart from the list a labeller sees).
    """
    if not 0.0 < fraction <= 1.0:
        raise TriageError("fraction must be in (0, 1]")
    if len(timestamps) != len(rows):
        raise TriageError("timestamps and rows must have the same length")
    count = len(timestamps)
    if count == 0:
        return []
    rng = random.Random(f"{seed}:{fixture_id}")
    sampled = set(rng.sample(range(count), max(1, math.ceil(fraction * count))))
    chosen = []
    for index, (stamp, row) in enumerate(zip(timestamps, rows)):
        reasons = []
        if index == 0:
            reasons.append("seed")
        if row["flags"]:
            reasons.append("flagged")
        if index in sampled:
            reasons.append("random")
        if reasons:
            chosen.append({"fixture_id": fixture_id, "index": index + 1, "timestamp_s": stamp,
                           "reasons": reasons})
    return chosen


def wilson_interval(successes: int, trials: int, z: float = WILSON_Z) -> tuple[float, float] | None:
    """95% Wilson interval; None for an empty denominator (never reported as zero error)."""
    if trials <= 0:
        return None
    p = successes / trials
    denominator = 1 + z * z / trials
    centre = (p + z * z / (2 * trials)) / denominator
    half = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / denominator
    return (max(0.0, centre - half), min(1.0, centre + half))


def calibrate(rows: Sequence[Mapping[str, Any]], labels: Mapping[float, Point],
              gate_px: float = GATE_PX, spread_limit_px: float = SPREAD_LIMIT_PX) -> dict[str, Any]:
    """How often does voter agreement land within the gate of the human label?

    Only rows whose timestamp has a human label count. An agreeing row has every voter present with
    a coordinate and a spread within `spread_limit_px`. Reports counts and intervals, never a verdict.
    """
    label_stamps = sorted(labels)
    labelled = []
    for row in rows:
        present, label = lookup(labels, label_stamps, row["timestamp_s"])
        if present:
            labelled.append((row, label))
    voter_names = sorted(rows[0]["positions"]) if rows else []
    agreeing, disagreeing = [], []
    for row, label in labelled:
        everyone = row["n_valid"] == len(voter_names)
        (agreeing if everyone and row["spread_px"] <= spread_limit_px else disagreeing).append((row, label))

    def within(group):
        return sum(1 for row, label in group if row["median_px"] is not None
                   and _distance(row["median_px"], label) <= gate_px)

    def interval(successes, trials):
        result = wilson_interval(successes, trials)
        return None if result is None else [round(result[0], 6), round(result[1], 6)]

    per_voter = {}
    for name in voter_names:
        errors = [_distance(row["positions"][name], label) for row, label in labelled
                  if row["positions"][name] is not None]
        per_voter[name] = {"n_with_coordinate": len(errors),
                           "n_lost_or_missing": len(labelled) - len(errors),
                           "mean_error_px": None if not errors else round(sum(errors) / len(errors), 6),
                           "max_error_px": None if not errors else round(max(errors), 6)}
    return {
        "gate_px": gate_px, "spread_limit_px": spread_limit_px, "voters": voter_names,
        "n_labelled_rows": len(labelled),
        "agreeing": {"n": len(agreeing), "median_within_gate": within(agreeing),
                     "wilson95": interval(within(agreeing), len(agreeing))},
        "not_agreeing": {"n": len(disagreeing), "median_within_gate": within(disagreeing),
                         "wilson95": interval(within(disagreeing), len(disagreeing))},
        "per_voter": per_voter,
    }
