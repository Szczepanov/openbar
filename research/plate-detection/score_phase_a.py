#!/usr/bin/env python3
"""Score Phase A candidates against human labels, descriptively (#233).

Per method and clip:

- frame recall: labelled frames where any candidate is the labelled plate (``identity-loose-v1``);
- frame top-1: labelled frames where the highest-scoring candidate is the labelled plate;
- clip selection on the decision frame (the first labelled frame), by ``top1`` and ``motion-v1``;
- geometry of the best-matching candidate: centre error and radius error, relative to the true
  radius;
- per-frame latency;
- the ``close-score-v1`` refusal on the label-free frame 0 (Phase A.2): refused clips are reported
  separately, with what plausible top-1 would have chosen, and frame identity is also counted over
  the clips that were not refused;
- clips where every labelled frame is on target, and lost frames (no candidate at all).

Phase A.2 track records hold at most the tracked circle; their frame 0 also carries the seed
detector's candidates and the run's recorded seed decision (``selected_index`` and ``refusal``),
which frame-0 identity and the refusal read instead, so each seed rule is scored as it ran
(``close-score-v1`` for ``track_sam2``, ``sam3-margin-v2`` for ``track_sam2_sam3seed``).

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
        "lost_frames": sum(not r["candidates"] for r in records),
        "frame0_refusal": frame0_refusal(records[0]) if records else None,
        **({"frame0_close_score_refusal": records[0]["seed"]["close_score_refusal"]}
           if records and "close_score_refusal" in records[0].get("seed", {}) else {}),
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
        if "seed" in first:  # the track's frame-0 choice is the seed the run recorded
            frame0[RULE_PLAUSIBLE_TOP1] = is_target(seed_choice(first), first_point)
    return {**out, "scored": True, "frame_recall": recall, "frame_top1": top1,
            "frame_plausible_top1": plausible_top1, "decision_time_s": decision["time_s"],
            "decision_from_label": decision["frame_index"] != first["frame_index"],
            "decision_has_target": best_match(candidates_of(decision), point) is not None,
            "clip": {name: is_target(chosen, point) for name, chosen in rules.items()}, "motion": motion_info,
            "clip_frame0": frame0, "centre_error_rel": centre_rel, "radius_error_rel": radius_rel}


def _plausible(record: dict[str, Any]) -> list[selection.Candidate]:
    return selection.plausible(candidates_of(record), record["width_px"], record["height_px"])


def _plausible_seed(record: dict[str, Any]) -> list[selection.Candidate]:
    seed = [selection.Candidate.from_dict(row) for row in record["seed"]["candidates"]]
    return selection.plausible(seed, record["width_px"], record["height_px"])


def seed_choice(record: dict[str, Any]) -> selection.Candidate | None:
    """The seed the run recorded on frame 0; records without one fall back to the plausible top-1."""
    seed = record["seed"]
    if "selected_index" not in seed:
        return selection.select_top1(_plausible_seed(record))
    index = seed["selected_index"]
    return None if index is None else selection.Candidate.from_dict(seed["candidates"][index])


def frame0_refusal(record: dict[str, Any]) -> dict[str, Any]:
    """The refusal a track run recorded on frame 0, else ``close-score-v1`` over the plausible
    candidates the detector saw on frame 0 (label-free)."""
    if "seed" in record:
        return record["seed"].get("refusal") or selection.refusal(_plausible_seed(record))
    return selection.refusal(_plausible(record))


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


def aggregate(per_clip: dict[str, dict[str, Any]], recorded_refusal: dict[str, Any] | None = None) -> dict[str, Any]:
    scored = [c for c in per_clip.values() if c["scored"]]
    frames = sum(c["labelled_frames"] for c in scored)
    centre = [v for c in scored for v in c["centre_error_rel"]]
    radius = [abs(v) for c in scored for v in c["radius_error_rel"]]
    accepted = [c for c in scored if not c["frame0_refusal"]["refused"]]
    with0 = [c for c in scored if c["clip_frame0"] is not None]
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
        "clips_every_frame_on_target": sum(c["frame_plausible_top1"] == c["labelled_frames"] for c in scored),
        "lost_frames": sum(c["lost_frames"] for c in per_clip.values()),
        "sampled_frames": sum(c["sampled_frames"] for c in per_clip.values()),
        "refusal": {
            **refusal_rule(per_clip, recorded_refusal),
            "refused_clips": sorted(k for k, c in per_clip.items() if c["scored"] and c["frame0_refusal"]["refused"]),
            "frame0_accepted": sum(not c["frame0_refusal"]["refused"] for c in with0),
            "frame0_accepted_correct": sum(c["clip_frame0"][RULE_PLAUSIBLE_TOP1] for c in with0
                                           if not c["frame0_refusal"]["refused"]),
            "frame0_refused": sum(c["frame0_refusal"]["refused"] for c in with0),
            "frame0_refused_would_be_correct": sum(c["clip_frame0"][RULE_PLAUSIBLE_TOP1] for c in with0
                                                   if c["frame0_refusal"]["refused"]),
            "accepted_labelled_frames": sum(c["labelled_frames"] for c in accepted),
            "accepted_frame_plausible_top1": sum(c["frame_plausible_top1"] for c in accepted),
        },
        "centre_error_rel_median": _quantile(centre, 0.5), "centre_error_rel_p90": _quantile(centre, 0.9),
        "radius_abs_error_rel_median": _quantile(radius, 0.5), "radius_abs_error_rel_p90": _quantile(radius, 0.9),
        "latency_s_median": statistics.median(latency) if latency else None,
        "latency_s_p90": _quantile(latency, 0.9),
        "candidates_per_frame_median": statistics.median(counts) if counts else None,
    }


REFUSAL_CONSTANTS = {selection.REFUSAL_RULE: {"score_ratio": selection.REFUSAL_SCORE_RATIO},
                     selection.MARGIN_RULE: {"margin_score": selection.MARGIN_SCORE,
                                             "concentric_centre_fraction": selection.SAME_SPOT_CENTRE_FRACTION,
                                             "score_decimals": selection.SCORE_DECIMALS}}


def refusal_rule(per_clip: dict[str, dict[str, Any]], recorded: dict[str, Any] | None = None) -> dict[str, Any]:
    """The one refusal rule behind these clips' frame-0 decisions, with the constants the run recorded in
    ``run.json`` (the current code's constants only for runs that recorded none), plus how the
    ``close-score-v1`` comparison the run recorded beside it would have refused."""
    rules = {c["frame0_refusal"]["rule"] for c in per_clip.values() if c.get("frame0_refusal")}
    if not rules and recorded:
        rules = {recorded["rule"]}
    if len(rules) != 1:
        raise SystemExit(f"cannot name one refusal rule for this method: {sorted(rules)}")
    rule = rules.pop()
    if recorded is not None and recorded["rule"] != rule:
        raise SystemExit(f"run.json records refusal rule {recorded['rule']!r}, the records say {rule!r}")
    result = ({k: v for k, v in recorded.items() if k != "also_recorded"} if recorded
              else {"rule": rule, **REFUSAL_CONSTANTS[rule]})
    scored = {k: c for k, c in per_clip.items() if c["scored"]}
    compared = {k: c for k, c in scored.items() if "frame0_close_score_refusal" in c}
    if compared:
        result["close_score_comparison"] = {
            "rule": selection.REFUSAL_RULE, "score_ratio": selection.REFUSAL_SCORE_RATIO,
            "refused_clips": sorted(k for k, c in compared.items() if c["frame0_close_score_refusal"]["refused"])}
    return result


def load_records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def score_run(run_dir: Path, private_root: Path, roles: dict[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {"match_rule": selection.MATCH_RULE, "motion_rule": selection.MOTION_RULE, "methods": {}}
    record_path = run_dir / "run.json"  # main() always has one; unit tests may score a bare directory
    recorded_methods = (json.loads(record_path.read_text(encoding="utf-8")).get("methods", {})
                        if record_path.is_file() else {})
    for method_dir in sorted(p for p in (run_dir / "candidates").iterdir() if p.is_dir()):
        recorded = recorded_methods.get(method_dir.name, {}).get("refusal")
        per_clip = {}
        for path in sorted(method_dir.glob("*.jsonl")):
            fixture_id = path.name.removesuffix(".jsonl")
            if fixture_id not in roles:
                raise SystemExit(f"{path} is not listed in run.json; refusing to score an unrecorded clip")
            per_clip[fixture_id] = {"role": roles[fixture_id],
                                    **score_clip(load_records(path), truth.load_truth(fixture_id, private_root))}
        by_role = {role: aggregate({k: v for k, v in per_clip.items() if v["role"] == role}, recorded)
                   for role in sorted({v["role"] for v in per_clip.values()})}
        result["methods"][method_dir.name] = {"all": aggregate(per_clip, recorded), "by_role": by_role,
                                              "per_clip": per_clip}
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
    lines += ["", "| Method | Frame-0 refused (would be correct) | Frame-0 accepted correct | "
              "Accepted-clip frame plausible top-1 | Clips with every labelled frame on target | Lost frames |",
              "|---" * 6 + "|"]
    for name, data in result["methods"].items():
        a, r = data["all"], data["all"]["refusal"]
        lines.append(f"| {name} | {r['frame0_refused']} ({r['frame0_refused_would_be_correct']}) | "
                     f"{r['frame0_accepted_correct']}/{r['frame0_accepted']} | "
                     f"{r['accepted_frame_plausible_top1']}/{r['accepted_labelled_frames']} | "
                     f"{a['clips_every_frame_on_target']}/{a['scored_clips']} | "
                     f"{a['lost_frames']}/{a['sampled_frames']} |")
    return "\n".join(lines)


def per_clip_table(methods: dict[str, dict[str, Any]]) -> str:
    """Per scored clip and method: frame plausible top-1 over labelled frames, the frame-0 outcome
    (``ok``, ``miss``, or ``-`` without a frame-0 label) and ``R`` when frame 0 was refused."""
    names = list(methods)
    scored = sorted({k for m in methods.values() for k, c in m["per_clip"].items() if c["scored"]})
    lines = ["| Clip | " + " | ".join(names) + " |", "|---" * (1 + len(names)) + "|"]
    for clip in scored:
        cells = []
        for name in names:
            c = methods[name]["per_clip"].get(clip)
            if c is None or not c["scored"]:
                cells.append("–")
                continue
            f0 = "-" if c["clip_frame0"] is None else ("ok" if c["clip_frame0"][RULE_PLAUSIBLE_TOP1] else "miss")
            refused = " R" if c["frame0_refusal"]["refused"] else ""
            cells.append(f"{c['frame_plausible_top1']}/{c['labelled_frames']} {f0}{refused}")
        lines.append(f"| {clip} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--run-dir", type=Path, required=True)
    p.add_argument("--repo-root", type=Path, required=True, help="checkout holding validation/private labels")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--compare", type=Path, action="append", default=[],
                   help="another run's score.json; its methods join the printed per-clip table only")
    args = p.parse_args(argv)
    roles = json.loads((args.run_dir / "run.json").read_text(encoding="utf-8"))["clips"]
    result = score_run(args.run_dir, args.repo_root / "validation" / "private", roles)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(table(result))
    methods: dict[str, Any] = {}
    for path in args.compare:
        methods.update(json.loads(path.read_text(encoding="utf-8"))["methods"])
    methods.update(result["methods"])
    print()
    print(per_clip_table(methods))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
