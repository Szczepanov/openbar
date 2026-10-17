#!/usr/bin/env python3
"""Score Phase A candidates against human labels, descriptively (#233).

Per method and clip:

- frame recall: labelled frames where any candidate is the labelled plate (``identity-loose-v1``);
- frame top-1: labelled frames where the highest-scoring candidate is the labelled plate;
- clip selection on the decision frame (the first labelled frame), by ``top1`` and ``motion-v1``;
- geometry of the best-matching candidate: centre error and radius error, relative to the true
  radius;
- per-frame latency.

No pass/fail verdict, no thresholds tuned here. Development clips only, so nothing generalises.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any, Sequence

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import selection  # noqa: E402
import truth  # noqa: E402

TIME_TOLERANCE_S = 0.004  # labels carry decoded frame times; this only absorbs rounding
RULE_TOP1 = "top1"
RULE_PLAUSIBLE_TOP1 = "plausible_top1"
RULE_LARGEST = "largest_plausible"
RULE_MOTION = "motion"
RULE_PLAUSIBLE_MOTION = "plausible_motion"
RULES = (RULE_TOP1, RULE_PLAUSIBLE_TOP1, RULE_LARGEST, RULE_MOTION, RULE_PLAUSIBLE_MOTION)


def candidates_of(record: dict[str, Any]) -> list[selection.Candidate]:
    return [selection.Candidate.from_dict(row) for row in record["candidates"]]


def labelled_frames(records: Sequence[dict[str, Any]], points: Sequence[truth.TruthPoint]
                    ) -> list[tuple[dict[str, Any], truth.TruthPoint]]:
    """Pair each truth point with the decoded frame at its time; unmatched points are dropped."""
    pairs = []
    for point in points:
        nearest = min(records, key=lambda r: (abs(r["time_s"] - point.timestamp_s), r["frame_index"]), default=None)
        if nearest is not None and abs(nearest["time_s"] - point.timestamp_s) <= TIME_TOLERANCE_S:
            pairs.append((nearest, point))
    return pairs


def best_match(cands: Sequence[selection.Candidate], point: truth.TruthPoint) -> selection.Candidate | None:
    hits = [c for c in cands if selection.match(c, point.center_x_px, point.center_y_px, point.radius_px)]
    return min(hits, key=lambda c: math.hypot(c.center_x_px - point.center_x_px, c.center_y_px - point.center_y_px),
               default=None)


def is_target(candidate: selection.Candidate | None, point: truth.TruthPoint) -> bool:
    return candidate is not None and selection.match(candidate, point.center_x_px, point.center_y_px, point.radius_px)


def score_clip(records: Sequence[dict[str, Any]], points: Sequence[truth.TruthPoint]) -> dict[str, Any]:
    records = sorted(records, key=lambda r: r["frame_index"])
    pairs = labelled_frames(records, points)
    out: dict[str, Any] = {
        "sampled_frames": len(records), "labelled_frames": len(pairs),
        "latency_s": [r["latency_s"] for r in records],
        "candidates_per_frame": [len(r["candidates"]) for r in records],
    }
    if not pairs:
        return {**out, "scored": False}
    recall = top1 = plausible_top1 = 0
    centre_rel, radius_rel = [], []
    for record, point in pairs:
        cands = candidates_of(record)
        hit = best_match(cands, point)
        if hit is not None:
            recall += 1
            centre_rel.append(math.hypot(hit.center_x_px - point.center_x_px,
                                         hit.center_y_px - point.center_y_px) / point.radius_px)
            radius_rel.append((hit.radius_px - point.radius_px) / point.radius_px)
        top1 += is_target(selection.select_top1(cands), point)
        plausible_top1 += is_target(selection.select_top1(_plausible(record)), point)
    decision, point = pairs[0]
    rules, motion_info = clip_rules(decision, [r for r in records if r["frame_index"] != decision["frame_index"]])
    first = records[0]  # label-free decision frame: the plan always holds the first decoded frame
    first_point = next((pt for rec, pt in pairs if rec["frame_index"] == first["frame_index"]), None)
    frame0 = None
    if first_point is not None:
        frame0_rules, _ = clip_rules(first, records[1:])
        frame0 = {name: is_target(chosen, first_point) for name, chosen in frame0_rules.items()}
    return {**out, "scored": True, "frame_recall": recall, "frame_top1": top1,
            "frame_plausible_top1": plausible_top1, "decision_time_s": decision["time_s"],
            "decision_from_label": decision["frame_index"] != first["frame_index"],
            "decision_has_target": best_match(candidates_of(decision), point) is not None,
            "clip": {name: is_target(chosen, point) for name, chosen in rules.items()}, "motion": motion_info,
            "clip_frame0": frame0, "centre_error_rel": centre_rel, "radius_error_rel": radius_rel}


def _plausible(record: dict[str, Any]) -> list[selection.Candidate]:
    return selection.plausible(candidates_of(record), record["width_px"], record["height_px"])


def clip_rules(decision: dict[str, Any], others: Sequence[dict[str, Any]]
               ) -> tuple[dict[str, selection.Candidate | None], dict[str, Any]]:
    """Every label-free rule's choice on the decision frame; none of them sees labels."""
    raw = candidates_of(decision)
    kept = _plausible(decision)
    motion, motion_info = selection.select_motion(raw, [candidates_of(r) for r in others])
    motion_kept, _ = selection.select_motion(kept, [_plausible(r) for r in others])
    return {RULE_TOP1: selection.select_top1(raw), RULE_PLAUSIBLE_TOP1: selection.select_top1(kept),
            RULE_LARGEST: selection.select_largest(kept), RULE_MOTION: motion,
            RULE_PLAUSIBLE_MOTION: motion_kept}, motion_info


def _quantile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))]


def aggregate(per_clip: dict[str, dict[str, Any]]) -> dict[str, Any]:
    scored = [c for c in per_clip.values() if c["scored"]]
    frames = sum(c["labelled_frames"] for c in scored)
    centre = [v for c in scored for v in c["centre_error_rel"]]
    radius = [abs(v) for c in scored for v in c["radius_error_rel"]]
    latency = [v for c in per_clip.values() for v in c["latency_s"]]
    counts = [v for c in per_clip.values() for v in c["candidates_per_frame"]]
    return {
        "clips": len(per_clip), "scored_clips": len(scored), "labelled_frames": frames,
        "frame_recall": sum(c["frame_recall"] for c in scored), "frame_top1": sum(c["frame_top1"] for c in scored),
        "frame_plausible_top1": sum(c["frame_plausible_top1"] for c in scored),
        "clip_decision_has_target": sum(c["decision_has_target"] for c in scored),
        "clip": {rule: sum(c["clip"][rule] for c in scored) for rule in RULES},
        "decision_from_label": sorted(k for k, c in per_clip.items() if c["scored"] and c["decision_from_label"]),
        "frame0_clips": sum(c["clip_frame0"] is not None for c in scored),
        "clip_frame0": {rule: sum(c["clip_frame0"][rule] for c in scored if c["clip_frame0"] is not None)
                        for rule in RULES},
        "centre_error_rel_median": _quantile(centre, 0.5), "centre_error_rel_p90": _quantile(centre, 0.9),
        "radius_abs_error_rel_median": _quantile(radius, 0.5), "radius_abs_error_rel_p90": _quantile(radius, 0.9),
        "latency_s_median": statistics.median(latency) if latency else None,
        "latency_s_p90": _quantile(latency, 0.9),
        "candidates_per_frame_median": statistics.median(counts) if counts else None,
    }


def load_records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def score_run(run_dir: Path, private_root: Path, roles: dict[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {"match_rule": selection.MATCH_RULE, "motion_rule": selection.MOTION_RULE, "methods": {}}
    for method_dir in sorted(p for p in (run_dir / "candidates").iterdir() if p.is_dir()):
        per_clip = {}
        for path in sorted(method_dir.glob("*.jsonl")):
            fixture_id = path.name.removesuffix(".jsonl")
            if fixture_id not in roles:
                raise SystemExit(f"{path} is not listed in run.json; refusing to score an unrecorded clip")
            per_clip[fixture_id] = {"role": roles[fixture_id],
                                    **score_clip(load_records(path), truth.load_truth(fixture_id, private_root))}
        by_role = {role: aggregate({k: v for k, v in per_clip.items() if v["role"] == role})
                   for role in sorted({v["role"] for v in per_clip.values()})}
        result["methods"][method_dir.name] = {"all": aggregate(per_clip), "by_role": by_role, "per_clip": per_clip}
    return result


def table(result: dict[str, Any]) -> str:
    fmt = (lambda v: "–" if v is None else f"{v:.3f}")
    lines = ["| Method | Clips | Target on decision frame | " + " | ".join(RULES) +
             " | Frame-0 plausible top-1 | Frame recall | Frame top-1 | Frame plausible top-1 | Centre err/r (median) | Radius err (median) | Latency/frame (median) |",
             "|---" * (11 + len(RULES)) + "|"]
    for name, data in result["methods"].items():
        a = data["all"]
        n, f = a["scored_clips"], a["labelled_frames"]
        rules = " | ".join(f"{a['clip'][rule]}/{n}" for rule in RULES)
        frame0 = f"{a['clip_frame0'][RULE_PLAUSIBLE_TOP1]}/{a['frame0_clips']}"
        lines.append(f"| {name} | {n} | {a['clip_decision_has_target']}/{n} | {rules} | {frame0} | "
                     f"{a['frame_recall']}/{f} | {a['frame_top1']}/{f} | {a['frame_plausible_top1']}/{f} | "
                     f"{fmt(a['centre_error_rel_median'])} | {fmt(a['radius_abs_error_rel_median'])} | "
                     f"{fmt(a['latency_s_median'])} s |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--run-dir", type=Path, required=True)
    p.add_argument("--repo-root", type=Path, required=True, help="checkout holding validation/private labels")
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args(argv)
    roles = json.loads((args.run_dir / "run.json").read_text(encoding="utf-8"))["clips"]
    result = score_run(args.run_dir, args.repo_root / "validation" / "private", roles)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(table(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
