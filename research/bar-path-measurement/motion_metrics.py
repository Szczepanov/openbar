"""Research diagnostics only; canonical ROM/velocity/benchmark semantics stay in Rust.

Exact stored timestamps match observations; sparse labels measure sparse intervals,
never frame-to-frame jitter. Any intervening loss or excessive observation gap breaks
an interval. Offsets are diagnostic and are never applied to prediction output.
"""
from __future__ import annotations

import bisect
import math
import statistics

from study_io import number, samples

AXES = ("x_px", "y_px")


def distribution(values: list[float]) -> dict:
    ordered = sorted(values)
    if not ordered:
        return dict(count=0, mae=None, rmse=None, p50=None, p90=None, maximum=None, bias=None)
    return {
        "count": len(values), "mae": statistics.fmean(abs(x) for x in values),
        "rmse": math.sqrt(statistics.fmean(x * x for x in values)),
        "p50": sorted(abs(x) for x in values)[math.ceil(len(values) * .5) - 1],
        "p90": sorted(abs(x) for x in values)[math.ceil(len(values) * .9) - 1],
        "maximum": max(abs(x) for x in values), "bias": statistics.fmean(values),
    }


def correlation(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3:
        return None
    am, bm = statistics.fmean(a), statistics.fmean(b)
    aa = sum((v - am) ** 2 for v in a)
    bb = sum((v - bm) ** 2 for v in b)
    if aa == 0 or bb == 0:
        return None
    return max(-1., min(1., sum((x - am) * (y - bm) for x, y in zip(a, b)) / math.sqrt(aa * bb)))


def evaluate(labels: list[dict], predicted: list[dict], *, max_gap_s: float = .2,
             seed_timestamp_s: float | None = None, stationary_windows: list[list[float]] | None = None) -> dict:
    samples(predicted)
    if number(max_gap_s, "max_gap_s", 0) == 0:
        raise ValueError("max_gap_s must be positive")
    if seed_timestamp_s is not None:
        number(seed_timestamp_s, "seed_timestamp_s", 0)
    previous = -1.
    for row in labels:
        t = number(row.get("timestamp_s"), "label timestamp_s", 0)
        if t <= previous:
            raise ValueError("label timestamps must increase strictly")
        previous = t
        if row.get("annotation_state") == "labelled":
            for axis in AXES:
                number(row["center_px"][axis], axis, 0)
        elif row.get("annotation_state") not in ("unlabelable", "not_annotated") or "center_px" in row:
            raise ValueError("invalid non-labelled observation")
    by_time = {s["timestamp_s"]: s for s in predicted}
    times = list(by_time)
    rows = [r for r in labels if r["timestamp_s"] != seed_timestamp_s]
    error_rows = []
    for label in rows:
        actual = by_time.get(label["timestamp_s"])
        if label["annotation_state"] != "labelled" or not actual or actual["state"] != "tracked":
            continue
        error = {a: actual["center_px"][a] - label["center_px"][a] for a in AXES}
        error_rows.append({"timestamp_s": label["timestamp_s"], "error_px": error,
                           "confidence": actual["confidence"]})
    offsets = {a: statistics.median(r["error_px"][a] for r in error_rows) if error_rows else None for a in AXES}
    intervals, unavailable = [], []
    for left, right in zip(rows, rows[1:]):
        t0, t1 = left["timestamp_s"], right["timestamp_s"]
        reason = None
        if left["annotation_state"] != "labelled" or right["annotation_state"] != "labelled":
            reason = "unlabelled_endpoint"
        elif t0 not in by_time or t1 not in by_time:
            reason = "unmatched_timestamp"
        else:
            segment = predicted[bisect.bisect_left(times, t0):bisect.bisect_right(times, t1)]
            if any(s["state"] != "tracked" for s in segment):
                reason = "loss_in_interval"
            elif any(b["timestamp_s"] - a["timestamp_s"] > max_gap_s for a, b in zip(segment, segment[1:])):
                reason = "observation_gap"
        if reason:
            unavailable.append({"start_s": t0, "end_s": t1, "reason": reason})
            continue
        label_delta = {a: right["center_px"][a] - left["center_px"][a] for a in AXES}
        error_delta = {a: by_time[t1]["center_px"][a] - by_time[t0]["center_px"][a] - label_delta[a] for a in AXES}
        mean_error = {a: ((by_time[t1]["center_px"][a] - right["center_px"][a]) +
                          (by_time[t0]["center_px"][a] - left["center_px"][a])) / 2 for a in AXES}
        frames = (right["frame_index"] - left["frame_index"]
                  if "frame_index" in left and "frame_index" in right else None)
        intervals.append({"start_s": t0, "end_s": t1, "duration_s": t1 - t0,
                          "frame_interval": frames, "delta_error_px": error_delta,
                          "label_velocity_px_s": {a: label_delta[a] / (t1 - t0) for a in AXES},
                          "mean_position_error_px": mean_error})
    jitter = []
    for window in stationary_windows or []:
        if len(window) != 2 or number(window[1], "window end_s", 0) <= number(window[0], "window start_s", 0):
            raise ValueError("stationary windows must have increasing finite endpoints")
        selected = [s for s in predicted if window[0] <= s["timestamp_s"] <= window[1]]
        dense = {s["timestamp_s"]: s for s in rows if s["annotation_state"] == "labelled"}
        reason = None
        if len(selected) < 3 or any(s["timestamp_s"] not in dense for s in selected):
            reason = "dense_labels_required"
        elif any(not isinstance(dense[s["timestamp_s"]].get("frame_index"), int) or
                 isinstance(dense[s["timestamp_s"]].get("frame_index"), bool) for s in selected):
            reason = "decoder_frame_coverage_required"
        elif any(dense[b["timestamp_s"]]["frame_index"] != dense[a["timestamp_s"]]["frame_index"] + 1
                 for a, b in zip(selected, selected[1:])):
            reason = "dense_labels_required"
        elif any(s["state"] == "lost" for s in selected):
            reason = "loss_in_window"
        elif any(b["timestamp_s"] - a["timestamp_s"] > max_gap_s for a, b in zip(selected, selected[1:])):
            reason = "observation_gap"
        elif any(max(dense[s["timestamp_s"]]["center_px"][a] for s in selected) -
                 min(dense[s["timestamp_s"]]["center_px"][a] for s in selected) > .5 for a in AXES):
            reason = "labels_not_stationary"
        record = {"start_s": window[0], "end_s": window[1], "unsupported_reason": reason}
        record["error_sd_px"] = None if reason else {
            a: statistics.pstdev(s["center_px"][a] - dense[s["timestamp_s"]]["center_px"][a] for s in selected)
            for a in AXES}
        jitter.append(record)
    confidence = []
    for lower, upper in ((0., .5), (.5, .8), (.8, 1.)):
        bucket = [r for r in error_rows if lower <= r["confidence"] and
                  (r["confidence"] < upper or upper == 1.)]
        norms = [math.hypot(*(r["error_px"][a] for a in AXES)) for r in bucket]
        confidence.append({"lower": lower, "upper": upper, "center_error_px": distribution(norms),
                           "error_gt_3_px": sum(v > 3 for v in norms)})
    labelled_count = sum(r["annotation_state"] == "labelled" for r in rows)
    return {
        "labelled_samples": labelled_count, "matched_tracked_samples": len(error_rows),
        "availability": len(error_rows) / labelled_count if labelled_count else None,
        "center_error_px": distribution([math.hypot(*(r["error_px"][a] for a in AXES)) for r in error_rows]),
        "axis_error_px": {a: distribution([r["error_px"][a] for r in error_rows]) for a in AXES},
        "constant_offset_px": offsets,
        "debiased_center_error_px": distribution([
            math.hypot(*(r["error_px"][a] - offsets[a] for a in AXES)) for r in error_rows]),
        "delta_axis_error_px": {a: distribution([r["delta_error_px"][a] for r in intervals]) for a in AXES},
        "delta_position_error_px": distribution([math.hypot(*(r["delta_error_px"][a] for a in AXES)) for r in intervals]),
        "error_vs_label_velocity_correlation": {
            a: correlation([r["mean_position_error_px"][a] for r in intervals],
                           [r["label_velocity_px_s"][a] for r in intervals]) for a in AXES},
        "available_intervals": len(intervals), "unavailable_intervals": unavailable,
        "interval_availability": len(intervals) / (len(intervals) + len(unavailable)) if len(rows) > 1 else None,
        "stationary_jitter": jitter, "confidence_vs_error": confidence,
        "matched_errors": error_rows, "intervals": intervals,
    }


def paired(candidate: dict, baseline: dict) -> dict:
    result = {}
    for key, rows_key, value_key in (("center", "matched_errors", "error_px"),
                                     ("delta", "intervals", "delta_error_px")):
        def indexed(report):
            return {(r["timestamp_s"] if key == "center" else (r["start_s"], r["end_s"])):
                    math.hypot(*(r[value_key][a] for a in AXES)) for r in report[rows_key]}
        c, b = indexed(candidate), indexed(baseline)
        common = sorted(c.keys() & b.keys())
        result[key] = {"common_count": len(common), "candidate_error_px": distribution([c[t] for t in common]),
                       "baseline_error_px": distribution([b[t] for t in common]),
                       "candidate_minus_baseline_px": distribution([c[t] - b[t] for t in common])}
    return result


def relative_diagnostics(labels: list[dict], predicted: list[dict], edges: list[dict], *, max_gap_s=.2) -> dict:
    """Score measured edge sums on labelled intervals; integrate only unbroken chains."""
    samples(predicted)
    if number(max_gap_s, "max_gap_s", 0) == 0:
        raise ValueError("max_gap_s must be positive")
    times = [r["timestamp_s"] for r in predicted]
    by_time = {r["timestamp_s"]: r for r in labels if r["annotation_state"] == "labelled"}
    by_edge = {}
    for edge in edges:
        t0 = number(edge.get("previous_timestamp_s"), "previous_timestamp_s", 0)
        t1 = number(edge.get("timestamp_s"), "timestamp_s", 0)
        index = bisect.bisect_left(times, t1)
        if index == 0 or index >= len(times) or times[index] != t1 or times[index - 1] != t0 or t1 in by_edge:
            raise ValueError("relative edge must join unique consecutive observation timestamps")
        delta = edge.get("delta_px")
        if delta is not None:
            if not isinstance(delta, dict) or set(delta) != set(AXES):
                raise ValueError("relative delta must contain x_px and y_px")
            for a in AXES:
                number(delta[a], a)
            if number(edge.get("confidence"), "relative confidence", 0) > 1:
                raise ValueError("relative confidence must be <= 1")
        elif edge.get("confidence") is not None:
            raise ValueError("missing relative observations forbid confidence")
        by_edge[t1] = edge
    intervals = []
    unavailable = 0
    for left, right in zip(labels, labels[1:]):
        t0, t1 = left["timestamp_s"], right["timestamp_s"]
        start, end = bisect.bisect_left(times, t0), bisect.bisect_left(times, t1)
        if (left["annotation_state"] != "labelled" or right["annotation_state"] != "labelled" or
                start >= len(times) or end >= len(times) or times[start] != t0 or times[end] != t1):
            unavailable += 1
            continue
        chosen = [by_edge.get(t) for t in times[start + 1:end + 1]]
        if not chosen or any(e is None or e["delta_px"] is None or
                             e["timestamp_s"] - e["previous_timestamp_s"] > max_gap_s for e in chosen):
            unavailable += 1
            continue
        error = {a: sum(e["delta_px"][a] for e in chosen) -
                    (right["center_px"][a] - left["center_px"][a]) for a in AXES}
        intervals.append({"start_s": t0, "end_s": t1, "duration_s": t1 - t0,
                          "delta_error_px": error, "minimum_edge_confidence": min(e["confidence"] for e in chosen)})
    anchor, accumulated, drift = None, {a: 0. for a in AXES}, []
    for index, t in enumerate(times):
        edge = by_edge.get(t)
        if index == 0 or edge is None or edge["delta_px"] is None or t - times[index - 1] > max_gap_s:
            anchor = by_time.get(t)
            accumulated = {a: 0. for a in AXES}
            continue
        if anchor is None:
            anchor = by_time.get(times[index - 1])
        if anchor is None:
            continue
        for a in AXES:
            accumulated[a] += edge["delta_px"][a]
        if t in by_time:
            drift.append({"timestamp_s": t, "anchor_timestamp_s": anchor["timestamp_s"],
                          "error_px": {a: accumulated[a] - (by_time[t]["center_px"][a] - anchor["center_px"][a]) for a in AXES}})
    quality = []
    for lower, upper in ((0., .5), (.5, .8), (.8, 1.)):
        selected = [r for r in intervals if lower <= r["minimum_edge_confidence"] and
                    (r["minimum_edge_confidence"] < upper or upper == 1.)]
        errors = [math.hypot(*(r["delta_error_px"][a] for a in AXES)) for r in selected]
        quality.append({"lower": lower, "upper": upper, "delta_error_px": distribution(errors),
                        "error_gt_3_px": sum(v > 3 for v in errors)})
    return {
        "measured_edges": sum(e["delta_px"] is not None for e in edges), "total_edges": max(0, len(times) - 1),
        "available_intervals": len(intervals), "unavailable_intervals": unavailable,
        "delta_axis_error_px": {a: distribution([r["delta_error_px"][a] for r in intervals]) for a in AXES},
        "delta_position_error_px": distribution([math.hypot(*(r["delta_error_px"][a] for a in AXES)) for r in intervals]),
        "cumulative_drift_px": distribution([math.hypot(*(r["error_px"][a] for a in AXES)) for r in drift]),
        "intervals": intervals, "drift_samples": drift, "confidence_vs_delta_error": quality,
    }
