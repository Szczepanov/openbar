#!/usr/bin/env python3
"""Apply the registered decision rules of COLOUR_HUB_CUE_EXPERIMENT_PLAN.md to the run outputs.

Written before any arm result was read. Interpretations of the plan's wording, fixed here:
- coverage: matched tracked labels / labelled frames, seed frame excluded;
- centre MAE, maximum error and silent false tracks are compared on the common support with the
  baseline of the same run (the coarse stream written by `run`);
- the maximum-error rule is judged on the common support (the baseline maximum over all labels is
  reported as the lenient variant);
- silent false track: a labelled frame with centre error > 3 px and confidence >= 0.8 (the top bucket of
  the existing confidence-vs-error diagnostic); an arm must not have more of them than the baseline on
  any clip;
- hub arms (H, C+H) are unsupported for a clip when the hub is accepted on fewer than 50% of its
  labelled frames, and an unsupported clip makes the arm not promising;
- no coordinate where every gate failed: a lost-reason frame of the C arm must be a lost sample, and no
  arm may be tracked where the coarse input was lost.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0] / "bar-path-measurement"))
import clip_core  # noqa: E402
import experiment  # noqa: E402
import motion_metrics  # noqa: E402
import study_io as io  # noqa: E402

COVERAGE_MIN = 0.80
IMPROVEMENT_MIN_PX = 0.5
WORSE_MAX_PX = 0.25
MAX_ERROR_SLACK_PX = 2.0
FALSE_TRACK_ERROR_PX = 3.0
FALSE_TRACK_CONFIDENCE = 0.8
ARM_FILES = {"C": "colour-gate", "H": "hub-fused", "CH": "colour-gate-hub-fused",
             "C_control": "colour-gate-control"}


def _errors(report: Mapping[str, Any]) -> dict[float, tuple[float, float]]:
    return {r["timestamp_s"]: (math.hypot(r["error_px"]["x_px"], r["error_px"]["y_px"]), r["confidence"])
            for r in report["matched_errors"]}


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def compare_arm(arm: Mapping[str, Any], baseline: Mapping[str, Any]) -> dict[str, Any]:
    """Per-clip figures for one arm against the baseline, from two `motion_metrics.evaluate` reports."""
    a, b = _errors(arm), _errors(baseline)
    common = sorted(a.keys() & b.keys())
    arm_common, base_common = [a[t][0] for t in common], [b[t][0] for t in common]
    base_all = [e for e, _ in b.values()]

    def silent(errors, stamps):
        return sum(1 for t in stamps if errors[t][0] > FALSE_TRACK_ERROR_PX and errors[t][1] >= FALSE_TRACK_CONFIDENCE)

    labelled = arm["labelled_samples"]
    return {
        "labelled_frames": labelled, "matched_arm": arm["matched_tracked_samples"],
        "coverage": arm["matched_tracked_samples"] / labelled if labelled else None,
        "common_support": len(common),
        "baseline_mae_common_px": _mean(base_common), "arm_mae_common_px": _mean(arm_common),
        "improvement_px": None if not common else _mean(base_common) - _mean(arm_common),
        "baseline_max_common_px": max(base_common, default=None), "arm_max_common_px": max(arm_common, default=None),
        "baseline_max_all_px": max(base_all, default=None),
        "silent_false_tracks_baseline": silent(b, common), "silent_false_tracks_arm": silent(a, common),
    }


def judge(clips: Mapping[str, Mapping[str, Any]], hub_arm: bool) -> dict[str, Any]:
    """Apply the five rules to the three per-clip comparisons (plus hub support for hub arms)."""
    rows = list(clips.values())
    rules: dict[str, Any] = {}
    rules["1_coverage_at_least_80pct_each_clip"] = all(
        r["coverage"] is not None and r["coverage"] >= COVERAGE_MIN for r in rows)
    usable = all(r["improvement_px"] is not None for r in rows)
    improved = [r["improvement_px"] >= IMPROVEMENT_MIN_PX for r in rows] if usable else []
    not_worse = [r["improvement_px"] >= -WORSE_MAX_PX for r in rows] if usable else []
    rules["2_mae_lower_by_0.5px_on_two_of_three_and_not_worse_than_0.25_on_the_rest"] = bool(
        usable and sum(improved) >= 2 and all(i or n for i, n in zip(improved, not_worse)))
    rules["3_max_error_within_2px_of_baseline_common_support"] = bool(usable and all(
        r["arm_max_common_px"] <= r["baseline_max_common_px"] + MAX_ERROR_SLACK_PX for r in rows))
    rules["3b_lenient_variant_vs_baseline_max_over_all_labels"] = bool(usable and all(
        r["arm_max_common_px"] <= r["baseline_max_all_px"] + MAX_ERROR_SLACK_PX for r in rows))
    rules["4_silent_false_tracks_not_more_than_baseline_on_any_clip"] = all(
        r["silent_false_tracks_arm"] <= r["silent_false_tracks_baseline"] for r in rows)
    if hub_arm:
        rules["hub_supported_on_every_clip"] = all(r.get("hub_supported", False) for r in rows)
    primary = [v for k, v in rules.items() if not k.startswith("3b_")]
    return {"rules": rules, "promising": bool(len(rows) == 3 and all(primary))}


def gate_failure_without_coordinate(samples: Sequence[Mapping[str, Any]], coarse: Sequence[Mapping[str, Any]],
                                    c_diagnostics: Sequence[Mapping[str, Any]] | None = None,
                                    seed_time: float | None = None) -> bool:
    """Rule 5: tracked only where the coarse input was tracked; C also lost wherever a loss reason was set.

    The seed frame is the manual-seed anchor, not a measurement, so it is exempt.
    """
    coarse_state = {s["timestamp_s"]: s["state"] for s in coarse}
    for s in samples:
        if s["timestamp_s"] == seed_time:
            continue
        if s["state"] == "tracked" and coarse_state.get(s["timestamp_s"]) == "lost":
            return False
    if c_diagnostics is not None:
        reasons = {d["timestamp_s"]: d.get("loss_reason") for d in c_diagnostics}
        for s in samples:
            if s["timestamp_s"] != seed_time and s["state"] == "tracked" and reasons.get(s["timestamp_s"]):
                return False
    return True


def evaluate_clip(run_dir: Path, manifest: Path, annotation: Path, seed: Path, fixture: str) -> dict[str, Any]:
    item = io.fixture(io.load(manifest), fixture)
    manifest_doc = io.load(manifest)
    labels = io.annotation(annotation, manifest_doc, item)
    seed_doc = io.seed(seed, item)
    predictions = {name: io.prediction(run_dir / f"{stem}.prediction-v1.json", item)
                   for name, stem in {"baseline": "coarse", **ARM_FILES}.items()
                   if (run_dir / f"{stem}.prediction-v1.json").exists()}
    seed_time = experiment.seed_reference_timestamp(seed_doc, predictions["baseline"], labels)
    reports = {name: motion_metrics.evaluate(labels["samples"], doc["samples"], max_gap_s=0.2,
                                             seed_timestamp_s=seed_time) for name, doc in predictions.items()}
    sidecar = io.load(run_dir / "sidecar.json")
    label_times = [s["timestamp_s"] for s in labels["samples"]
                   if s["annotation_state"] == "labelled" and s["timestamp_s"] != seed_time]
    hub_share = clip_core.hub_labelled_share(sidecar["diagnostics"]["hub"], label_times)
    arms = {}
    for name in ("C", "H", "CH", "C_control"):
        if name in reports:
            arms[name] = compare_arm(reports[name], reports["baseline"])
            arms[name]["hub_supported"] = clip_core.hub_supported_for_scoring(hub_share)
            arms[name]["delta_mae_px"] = reports[name]["delta_position_error_px"]["mae"]
            arms[name]["baseline_delta_mae_px"] = reports["baseline"]["delta_position_error_px"]["mae"]
            arms[name]["gate_failure_without_coordinate_ok"] = gate_failure_without_coordinate(
                predictions[name]["samples"], predictions["baseline"]["samples"],
                sidecar["diagnostics"]["C"] if name in ("C", "C_control", "CH") else None, seed_time)
    return {"fixture": fixture, "seed_timestamp_s": seed_time, "hub_labelled_share": hub_share, "arms": arms}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--annotations-dir", type=Path, required=True)
    parser.add_argument("--seeds-dir", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True, help="contains one folder per fixture")
    parser.add_argument("--fixture", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        clips = {f: evaluate_clip(args.run_dir / f, args.manifest, args.annotations_dir / f"{f}.annotation-v1.json",
                                  args.seeds_dir / f"{f}.manual-target-seed-v1.json", f) for f in args.fixture}
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    verdicts = {}
    for arm in ("C", "H", "CH"):
        per_clip = {f: c["arms"][arm] for f, c in clips.items()}
        result = judge(per_clip, hub_arm=arm in ("H", "CH"))
        result["rules"]["5_no_coordinate_when_every_gate_fails"] = all(
            v["gate_failure_without_coordinate_ok"] for v in per_clip.values())
        result["promising"] = result["promising"] and result["rules"]["5_no_coordinate_when_every_gate_fails"]
        verdicts[arm] = result
    io.write(args.output, {"schema_version": 1, "plan": "COLOUR_HUB_CUE_EXPERIMENT_PLAN.md",
                           "clips": clips, "verdicts": verdicts})
    return 0


if __name__ == "__main__":
    sys.exit(main())
