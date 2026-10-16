"""Score one frozen plate-selection run against the frozen labels, once (#178).

Research only, standard library only. Inputs are a complete freeze record and the
``observations.json`` that ``plate_selection_eval.py run`` wrote for it. Labels are read here for
the first time, after every hash in the record has been re-checked. Outcome definitions follow
docs/plans/AUTOMATIC_PLATE_SELECTION_PLAN.md ("Freeze and scoring protocol") and the metrics in
docs/plans/HOUGH_TOP_K_PREPARATION.md, restricted to what this freeze can resolve:

- the reference is taken at the label row nearest the record's decision time (as the executor
  picks the frame): an adjudicated centre, or else the mean of passes a and b when they agree
  within Tc and Tr; pass radii must always agree within Tr; a clip the record marks unresolved,
  or disagreeing passes, leave it unresolved and a ``select`` case indeterminate;
- a geometry matches when ``hypot(dx, dy) <= Tc`` and ``|dr| <= Tr``, with Tc and Tr read from
  the record's ``scoring.geometry_reference`` text, never from the command line;
- identity is geometric only: no scene or rim inventory exists, so concentric or nearby
  distractors are not resolved and the wrong-target taxonomy is ``unknown`` for every case.

The result is descriptive. It carries no pass, promotion or threshold verdict.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plate_selection_eval as executor  # noqa: E402  (record loading and hashing rules)

SCORER_ID = "plate-selection-scorer-v1"
SCORE_FILE = "score.json"
Z = 1.959963984540054
KS = (1, 3, 5, 10, 20)
TOLERANCES = re.compile(r"\bTc\s*=\s*(\d+(?:\.\d+)?)\s*px\b.*?\bTr\s*=\s*(\d+(?:\.\d+)?)\s*px\b")
TIME_TOLERANCE_S = 0.0005  # decoder_match_tolerance_s of the label packages
NO_FRAME_TOLERANCE_S = 0.02  # without a decoded frame, a label row this close stands for it
REFUSE_POLICIES = {"refuse_ambiguous", "refuse_no_target", "refuse_no_pre_movement"}


class ScoreError(RuntimeError):
    """The run cannot be scored against this record."""


def wilson(successes: int, n: int) -> dict[str, Any]:
    """Two-sided 95% Wilson interval; an empty denominator is not estimable, never zero."""
    if n == 0:
        return {"x": successes, "n": 0, "rate": None, "low": None, "high": None}
    p = successes / n
    d = 1 + Z * Z / n
    centre = (p + Z * Z / (2 * n)) / d
    half = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / d
    return {"x": successes, "n": n, "rate": p, "low": max(0.0, centre - half), "high": min(1.0, centre + half)}


def summary(values: Iterable[float]) -> dict[str, Any]:
    data = sorted(values)
    if not data:
        return {"n": 0}
    return {"n": len(data), "median": statistics.median(data), "mean": statistics.fmean(data),
            "p95": data[math.ceil(0.95 * len(data)) - 1], "min": data[0], "max": data[-1],
            **({"q1": statistics.quantiles(data, n=4)[0], "q3": statistics.quantiles(data, n=4)[2]}
               if len(data) >= 2 else {})}


def tolerances(record: dict[str, Any]) -> tuple[float, float]:
    text = record["scoring"]["geometry_reference"]
    matches = TOLERANCES.findall(text if isinstance(text, str) else "")
    if len(matches) != 1:
        raise ScoreError("scoring.geometry_reference must state Tc=<px> and Tr=<px> exactly once")
    tc, tr = (float(value) for value in matches[0])
    if not (tc > 0 and tr > 0):
        raise ScoreError("Tc and Tr must be positive")
    return tc, tr


def read_pass(path: Path, decision_s: float, frame_index: int | None) -> dict[str, Any] | None:
    """The label row for the decision frame; None when no row can stand for it.

    With a decoded frame the row is chosen by that frame's index, so a requested time between
    frames and single-row packages both work. Without one (an executor error), the row nearest the
    requested time is used only within ``NO_FRAME_TOLERANCE_S``; otherwise the reference is
    unresolved. Geometry is None when that pass has none there.
    """
    with path.open(encoding="utf-8", newline="") as handle:
        rows = sorted(csv.DictReader(handle), key=lambda row: float(row["timestamp_s"]))
    times = [float(row["timestamp_s"]) for row in rows]
    if len(set(times)) != len(times) or len({row["frame_index"] for row in rows}) != len(rows):
        raise ScoreError(f"{path.name} labels one timestamp or frame twice")
    if frame_index is not None:
        chosen = [row for row in rows if int(row["frame_index"]) == frame_index]
        if not chosen:
            raise ScoreError(f"{path.name} has no row for the decoded frame {frame_index}")
        row = chosen[0]
    else:
        if not rows:
            return None
        row = min(rows, key=lambda item: (abs(float(item["timestamp_s"]) - decision_s), float(item["timestamp_s"])))
        if abs(float(row["timestamp_s"]) - decision_s) > NO_FRAME_TOLERANCE_S:
            return None
    labelled = row["annotation_state"] == "labelled" and bool(row["radius_px"])
    geometry = {key: float(row[key]) for key in ("x_px", "y_px", "radius_px")} if labelled else None
    if geometry is not None and (not all(math.isfinite(v) for v in geometry.values())
                                 or geometry["radius_px"] <= 0):
        raise ScoreError(f"{path.name} has invalid geometry at {decision_s} s")
    return {"state": row["annotation_state"], "timestamp_s": float(row["timestamp_s"]),
            "frame_index": int(row["frame_index"]), "geometry": geometry}


def read_label_sheet(path: Path) -> dict[str, dict[str, Any]]:
    """Per fixture: onsets and expected policies from passes a and b only."""
    sheet: dict[str, dict[str, Any]] = defaultdict(lambda: {"onsets": [], "policies": {}})
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("annotator") not in ("a", "b"):
                continue
            entry = sheet[row["fixture_id"]]
            entry["policies"][row["annotator"]] = row.get("expected_policy")
            if row.get("movement_onset_s"):
                onset = float(row["movement_onset_s"])
                if not math.isfinite(onset) or onset < 0:
                    raise ScoreError(f"{row['fixture_id']}: movement onset must be finite and >= 0")
                entry["onsets"].append(onset)
    return sheet


def read_adjudication(path: Path) -> dict[tuple[str, int], dict[str, Any]]:
    document = executor.load_json(path)
    frames = document.get("frames") if isinstance(document, dict) else None
    if not isinstance(frames, list):
        raise ScoreError(f"{path.name} has no frames list")
    keyed = {(frame["fixture_id"], int(frame["frame_index"])): frame for frame in frames}
    if len(keyed) != len(frames):
        raise ScoreError(f"{path.name} adjudicates one frame twice")
    return keyed


def reference(passes: list[dict[str, Any]], adjudicated: dict[str, Any] | None,
              tc: float, tr: float) -> tuple[dict[str, Any] | None, str]:
    """Reference geometry and how it was resolved; None when it is unresolved.

    An adjudication at the decision frame replaces the centre comparison (its centre, with the
    passes' mean radius), but the pass radii must still agree within Tr, because radius is not
    adjudicated and a concentric inner rim must never be averaged in. Without one, both passes
    need geometry and must agree within Tc and Tr, and the reference is their mean.
    """
    geometries = [entry["geometry"] for entry in passes]
    if adjudicated is not None and (adjudicated.get("status") == "unresolved" or adjudicated.get("x_px") is None):
        return None, "adjudication unresolved at the decision frame"
    if any(g is None for g in geometries):
        states = sorted({entry["state"] for entry in passes})
        return None, f"a pass has no geometry at the decision frame ({', '.join(states)})"
    a, b = geometries
    centre_gap = math.hypot(a["x_px"] - b["x_px"], a["y_px"] - b["y_px"])
    radius_gap = abs(a["radius_px"] - b["radius_px"])
    if radius_gap > tr:
        return None, f"pass radii disagree ({radius_gap:.2f} px); radius is not adjudicated"
    if adjudicated is not None:
        radius = statistics.fmean(g["radius_px"] for g in geometries)
        return ({"x_px": float(adjudicated["x_px"]), "y_px": float(adjudicated["y_px"]), "radius_px": radius,
                 "pass_centre_gap_px": centre_gap, "pass_radius_gap_px": radius_gap},
                "adjudicated centre, mean pass radius")
    if centre_gap > tc:
        return None, f"passes disagree (centre {centre_gap:.2f} px, radius {radius_gap:.2f} px)"
    mean = {key: statistics.fmean(g[key] for g in geometries) for key in ("x_px", "y_px", "radius_px")}
    return {**mean, "pass_centre_gap_px": centre_gap, "pass_radius_gap_px": radius_gap}, "mean of agreeing passes"


def geometry_error(cx: float, cy: float, radius: float, ref: dict[str, Any]) -> dict[str, float]:
    signed = radius - ref["radius_px"]
    return {"centre_error_px": math.hypot(cx - ref["x_px"], cy - ref["y_px"]), "radius_signed_px": signed,
            "radius_abs_px": abs(signed), "radius_relative_pct": 100 * signed / ref["radius_px"]}


def matches(geometry: dict[str, float], ref: dict[str, Any], tc: float, tr: float) -> bool:
    error = geometry_error(geometry["center_x_px"], geometry["center_y_px"], geometry["radius_px"], ref)
    return error["centre_error_px"] <= tc and error["radius_abs_px"] <= tr


def policy_group(policy: str) -> str:
    if policy == "select":
        return "S"
    if policy == "refuse_unsupported":
        return "U"
    if policy in REFUSE_POLICIES:
        return "R"
    return "I"


def membership(observation: dict[str, Any], ref: dict[str, Any], tc: float, tr: float) -> dict[str, Any]:
    """Stage membership and the K=20 funnel partition for one selectable case."""
    raw = [row for row in observation["raw"] if matches(row, ref, tc, tr)]
    retained = [row for row in raw if row["retained_first_60"]]
    ranked = [row["rank"] for row in observation["ranked"] if matches(row, ref, tc, tr)]
    first_rank = min(ranked) if ranked else None
    if first_rank == 1:
        partition = "top1_member"
    elif first_rank is not None and first_rank <= 20:
        partition = "rank_2_20_member"
    elif first_rank is not None:
        partition = "post_fit_rank_over_20"
    elif retained:
        partition = "fit_loss"
    elif raw:
        partition = "truncation_loss"
    else:
        partition = "raw_generation_loss"
    return {"raw_member": bool(raw), "post_first_60_member": bool(retained), "post_fit_member": bool(ranked),
            "first_matching_rank": first_rank, "matching_raw_indices": [row["raw_index"] for row in raw],
            "top_k_member": {str(k): first_rank is not None and first_rank <= k for k in KS},
            "ranked_available": len(observation["ranked"]), "funnel_k20": partition}


def score_clip(clip: dict[str, Any], entry: dict[str, Any], ref: dict[str, Any] | None, ref_rule: str,
               onsets: list[float], tc: float, tr: float) -> dict[str, Any]:
    """One mutually exclusive outcome. The group depends only on the record and labels, never the run."""
    group, group_reason = policy_group(clip["expected_policy"]), None
    if group == "S" and ref is None:
        group, group_reason = "I", f"unresolved reference: {ref_rule}"
    elif group == "S" and not onsets:
        group, group_reason = "I", "no movement onset label"
    final = entry.get("final") or {}
    emitted = final.get("status") == "suggested"
    result: dict[str, Any] = {"fixture_id": clip["fixture_id"], "session_id": clip["session_id"],
                              "challenge": clip["challenge"], "expected_policy": clip["expected_policy"],
                              "group": group, "group_reason": group_reason, "emitted": emitted,
                              "reason": final.get("reason"), "error": entry.get("error"),
                              "timed_out": bool(entry.get("timed_out")), "reference": ref,
                              "reference_rule": ref_rule,
                              "margin": (entry.get("observation") or {}).get("support_margin")}
    if entry.get("error") is not None or "observation" not in entry:
        result["outcome"] = "error"
        return result
    observation = entry["observation"]
    if ref is not None and emitted:
        result["final_geometry_error"] = geometry_error(final["center_x_px"], final["center_y_px"],
                                                        final["radius_px"], ref)
    if result["timed_out"]:
        result["outcome"] = "timeout"  # outside the frozen budget, whatever was emitted
    elif group in ("R", "U"):
        result["outcome"] = "unsafe_acceptance" if emitted else "correct_refusal"
    elif group == "I":
        result["outcome"] = "indeterminate_emitted" if emitted else "indeterminate_refused"
    elif not emitted:
        result["outcome"] = "missed_refused"
    elif not matches(final, ref, tc, tr):
        result["outcome"] = "wrong_target"
        result["wrong_target_class"] = "unknown"  # no independent scene inventory in this freeze
    elif entry["frame"]["actual_time_s"] >= min(onsets):
        result["outcome"] = "timing_failure"
    else:
        result["outcome"] = "accepted_correct"
    if group == "S":
        result["membership"] = membership(observation, ref, tc, tr)
        selected = observation.get("selected")
        result["pre_refinement_top1_correct"] = bool(selected) and matches(selected, ref, tc, tr)
        result["final_identity_correct"] = emitted and matches(final, ref, tc, tr)
    return result


def rates(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(row["outcome"] for row in rows)
    eligible = [row for row in rows if row["group"] == "S"]
    refusal = [row for row in rows if row["group"] in ("R", "U")]
    emitted_eligible = [row for row in eligible if row["emitted"]]
    count = lambda subset, name: sum(row["outcome"] == name for row in subset)  # noqa: E731
    return {
        "n_clips": len(rows), "sessions": sorted({row["session_id"] for row in rows}),
        "groups": dict(Counter(row["group"] for row in rows)), "outcomes": dict(counts),
        "emitted": sum(row["emitted"] for row in rows),
        # A timeout outranks other outcomes, so show emissions it hides (an unsafe one in R/U).
        "emitted_and_timed_out_by_group": dict(Counter(row["group"] for row in rows
                                                       if row["emitted"] and row["timed_out"])),
        "accepted_correct_per_eligible": wilson(count(eligible, "accepted_correct"), len(eligible)),
        "missed_refused_per_eligible": wilson(count(eligible, "missed_refused"), len(eligible)),
        "wrong_target_per_eligible": wilson(count(eligible, "wrong_target"), len(eligible)),
        "wrong_target_per_emitted_eligible": wilson(count(emitted_eligible, "wrong_target"), len(emitted_eligible)),
        "unsafe_acceptance_per_required_refusal": wilson(count(refusal, "unsafe_acceptance"), len(refusal)),
        "correct_refusal_per_required_refusal": wilson(count(refusal, "correct_refusal"), len(refusal)),
    }


def stage_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [row for row in rows if row["group"] == "S"]
    scored = [row for row in eligible if "membership" in row]
    n = len(eligible)  # every S case is a denominator, including errors
    member = lambda key: wilson(sum(row["membership"][key] for row in scored), n)  # noqa: E731
    return {
        "n_eligible": n, "n_with_observation": len(scored), "errors": n - len(scored),
        "raw_member": member("raw_member"), "post_first_60_member": member("post_first_60_member"),
        "post_fit_member": member("post_fit_member"),
        "top_k_member": {str(k): wilson(sum(row["membership"]["top_k_member"][str(k)] for row in scored), n)
                         for k in KS},
        "funnel_k20": dict(Counter(row["membership"]["funnel_k20"] for row in scored)),
        "pre_refinement_top1_correct": wilson(sum(row["pre_refinement_top1_correct"] for row in scored), n),
        "final_stage_changes": sum(row["pre_refinement_top1_correct"] != row["final_identity_correct"]
                                   for row in scored),
    }


def geometry_summaries(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for outcome in ("accepted_correct", "wrong_target"):
        errors = [row["final_geometry_error"] for row in rows if row["outcome"] == outcome and "final_geometry_error" in row]
        out[outcome] = {key: summary(error[key] for error in errors)
                        for key in ("centre_error_px", "radius_signed_px", "radius_abs_px", "radius_relative_pct")}
    return out


def margins(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[float]] = defaultdict(list)
    missing: Counter = Counter()
    for row in rows:
        if row["group"] == "S" and "pre_refinement_top1_correct" in row:
            key = "correct_top1" if row["pre_refinement_top1_correct"] else "wrong_top1"
        else:
            key = {"R": "required_refusal", "U": "unsupported", "I": "indeterminate"}.get(row["group"], "error")
        if row["margin"] is None:
            missing[key] += 1
        else:
            groups[key].append(row["margin"])
    return {key: {**summary(groups.get(key, [])), "missing": missing.get(key, 0)}
            for key in sorted(set(groups) | set(missing))}


def latency(entries: list[dict[str, Any]]) -> dict[str, Any]:
    with_latency = [entry["latency"] for entry in entries if "latency" in entry]
    return {"cold_search_s": summary(item["cold_search_s"] for item in with_latency),
            "warm_median_search_s": summary(item["warm_median_s"] for item in with_latency),
            "wall_s_including_failures": summary(entry["wall_s"] for entry in entries),
            "probe_s": summary(entry["frame"]["probe_s"] for entry in entries if "frame" in entry),
            "decode_s": summary(entry["frame"]["decode_s"] for entry in entries if "frame" in entry),
            "timeouts": sum(bool(entry.get("timed_out")) for entry in entries)}


def strata(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row[key]].append(row)
    return {name: rates(subset) for name, subset in sorted(grouped.items())}


def load_run(run_dir: Path, record_sha256: str) -> tuple[dict[str, Any], str]:
    path = run_dir / executor.OBSERVATIONS
    document = executor.load_json(path)
    if not isinstance(document, dict) or document.get("record_sha256") != record_sha256:
        raise ScoreError("observations were produced for a different record")
    started = executor.load_json(run_dir / executor.STARTED)
    if not isinstance(started, dict) or started.get("record_sha256") != record_sha256:
        raise ScoreError("run marker belongs to a different record")
    log = run_dir / executor.CLIP_LOG
    if not log.exists():
        raise ScoreError(f"{executor.CLIP_LOG} is missing; the executor always writes it")
    logged = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    if logged != document["clips"]:
        raise ScoreError(f"{executor.OBSERVATIONS} differs from the per-clip log {executor.CLIP_LOG}")
    return document, executor.sha256_file(path)


def clip_reference(clip: dict[str, Any], entry: dict[str, Any], repo_root: Path,
                   adjudications: dict[str, dict], tc: float, tr: float) -> tuple[dict[str, Any] | None, str]:
    """Reference for the decision frame; the policy group never depends on whether decoding worked."""
    decision_s = float(clip["decision_frame_pts_s"])
    frame = entry.get("frame")
    if frame is not None and abs(float(frame["requested_time_s"]) - decision_s) > 1e-9:
        raise ScoreError(f"{clip['fixture_id']}: the run requested a different time from the record")
    frame_index = None if frame is None else int(frame["frame_index"])
    passes = [read_pass(repo_root / label["path"], decision_s, frame_index)
              for label in sorted(clip["label_files"], key=lambda label: label["annotator"])]
    if any(entry is None for entry in passes):
        return None, f"no label row within {NO_FRAME_TOLERANCE_S} s of the decision time and no decoded frame"
    if len({entry["frame_index"] for entry in passes}) != 1:
        raise ScoreError(f"{clip['fixture_id']}: passes a and b label different frames at the decision time")
    if frame is not None and abs(frame["actual_time_s"] - passes[0]["timestamp_s"]) > TIME_TOLERANCE_S:
        raise ScoreError(f"{clip['fixture_id']}: the labelled frame time differs from the decoded frame time")
    adjudication = clip["adjudication"]
    if adjudication["unresolved"]:
        return None, "the record marks this clip's adjudication unresolved"  # the frozen record wins
    frames = adjudications.get(adjudication["path"], {}) if adjudication["path"] is not None else {}
    return reference(passes, frames.get((clip["fixture_id"], passes[0]["frame_index"])), tc, tr)


def score(record_path: Path, record_sha256: str, repo_root: Path, run_dir: Path) -> Path:
    out = run_dir / SCORE_FILE
    if out.exists():
        raise ScoreError(f"{out} exists; a run is scored once")
    try:
        record = executor.load_record(record_path, record_sha256)
        executor.validate_structure(record)
        clips = record["inputs"]["clips"]
        hashed = [(record["inputs"]["labels_csv"]["path"], record["inputs"]["labels_csv"]["sha256"])]
        hashed += [(label["path"], label["sha256"]) for clip in clips for label in clip["label_files"]]
        hashed += [(clip["adjudication"]["path"], clip["adjudication"]["sha256"]) for clip in clips
                   if clip["adjudication"]["path"] is not None]
        for path, expected in hashed:
            if executor.sha256_file(repo_root / path) != expected:
                raise ScoreError(f"{path} does not match its sha256 in the record")
    except executor.FreezeError as error:
        raise ScoreError(str(error)) from error
    tc, tr = tolerances(record)
    gaps = record.get("known_gaps", [])
    if not isinstance(gaps, list) or not all(isinstance(gap, str) for gap in gaps):
        raise ScoreError("known_gaps must be a list of strings")
    document, observations_sha256 = load_run(run_dir, record_sha256)
    entries = {entry["fixture_id"]: entry for entry in document["clips"]}
    if sorted(entries) != sorted(clip["fixture_id"] for clip in clips) or len(entries) != len(document["clips"]):
        raise ScoreError("observations do not cover exactly the record's clips")
    sheet = read_label_sheet(repo_root / record["inputs"]["labels_csv"]["path"])
    adjudications = {path: read_adjudication(repo_root / path)
                     for path in {clip["adjudication"]["path"] for clip in clips} if path is not None}
    rows = []
    for clip in clips:
        entry = entries[clip["fixture_id"]]
        ref, rule = clip_reference(clip, entry, repo_root, adjudications, tc, tr)
        labelled = sheet.get(clip["fixture_id"], {"onsets": [], "policies": {}})
        row = score_clip(clip, entry, ref, rule, labelled["onsets"], tc, tr)
        row["label_sheet_policies"] = labelled["policies"]  # reported; the record's policy is frozen
        row["label_sheet_policy_differs"] = any(policy != clip["expected_policy"]
                                                for policy in labelled["policies"].values())
        rows.append(row)
    result = {
        "scorer": SCORER_ID, "mode": "descriptive", "freeze_id": record["freeze_id"],
        "record_sha256": record_sha256, "observations_sha256": observations_sha256,
        "tolerances_px": {"Tc": tc, "Tr": tr}, "match_rule": "hypot(dx, dy) <= Tc and |dr| <= Tr; identity geometric only",
        "reference_rule": "at the record's decision time: adjudicated centre (mean pass radius) if adjudicated, "
                          "else the mean of passes a and b when they agree within Tc/Tr; otherwise unresolved",
        "record_geometry_reference": record["scoring"]["geometry_reference"],
        "limitations": ["no scene or rim inventory: concentric or nearby distractors are not resolved",
                        "wrong-target taxonomy is unknown for every case",
                        "clips from one session are correlated; pooled intervals are conditional summaries",
                        "movement onset is compared in seconds from stream start, as the label packages use",
                        *gaps],
        "pooled": rates(rows), "by_session": strata(rows, "session_id"), "by_challenge": strata(rows, "challenge"),
        "stages": stage_metrics(rows), "geometry": geometry_summaries(rows), "margins": margins(rows),
        "latency": latency(list(entries.values())), "clips": rows,
    }
    text = json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"
    with out.open("x", encoding="utf-8", newline="\n") as handle:  # "x": never overwrite a score
        handle.write(text)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--record-sha256", required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=executor.ROOT)
    args = parser.parse_args(argv)
    try:
        print(score(args.record, args.record_sha256, args.repo_root, args.run_dir))
    except (ScoreError, OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        print(f"refused: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
