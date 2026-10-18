#!/usr/bin/env python3
"""Observe motion in completed development tracks; never select or replace a target.

CPU/stdlib only. No media, labels or models are opened. See
docs/analysis/PLATE_MOTION_OBSERVATION.md for the statistic and its limits.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import clips
from run_phase_a import sha256_file

RULE = "motion-observation-v1"
THRESHOLD_RADII = 1.5
MAX_GAP_S = 0.5


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return value


def _quantile(weighted, fraction):
    target = math.fsum(weight for _, weight in weighted) * fraction
    total = 0.0
    for value, weight in sorted(weighted):
        total += weight
        if total >= target:
            return value
    return max(value for value, _ in weighted)


def observe(records):
    """Time-weighted travel within uninterrupted segments, in the first valid radius.

    Each valid-to-valid interval of at most MAX_GAP_S weights its left endpoint.
    Losses and longer gaps split segments. No missing position is manufactured.
    """
    segments = []
    segment = []
    last_t = None
    first_t = None
    reference_radius = None
    valid_count = 0
    for record in records:
        t = _number(record["time_s"], "time_s")
        if t < 0 or (last_t is not None and t <= last_t):
            raise ValueError("timestamps must be nonnegative and strictly increasing")
        if first_t is None:
            first_t = t
        candidates = record["candidates"]
        if not isinstance(candidates, list) or len(candidates) > 1:
            raise ValueError("expected zero or one tracked candidate")
        if not candidates or (last_t is not None and t - last_t > MAX_GAP_S):
            if segment:
                segments.append(segment)
            segment = []
        if candidates:
            candidate = candidates[0]
            for key in ("center_x_px", "center_y_px", "radius_px", "score"):
                _number(candidate[key], key)
            if candidate["radius_px"] <= 0 or not 0 <= candidate["score"] <= 1:
                raise ValueError("radius must be positive and score must be in [0, 1]")
            if reference_radius is None:
                reference_radius = candidate["radius_px"]
            segment.append((t, candidate["center_y_px"]))
            valid_count += 1
        last_t = t
    if segment:
        segments.append(segment)
    details = []
    for segment in segments:
        if len(segment) < 2:
            continue
        weighted = [(left[1], right[0] - left[0]) for left, right in zip(segment, segment[1:])]
        ys = [y for _, y in segment]
        details.append({
            "start_s": segment[0][0], "end_s": segment[-1][0],
            "duration_s": segment[-1][0] - segment[0][0],
            "p02_p98_travel_radii": (_quantile(weighted, .98) - _quantile(weighted, .02)) / reference_radius,
            "range_radii": (max(ys) - min(ys)) / reference_radius,
        })
        for key in ("duration_s", "p02_p98_travel_radii", "range_radii"):
            _number(details[-1][key], key)
    travel = max((s["p02_p98_travel_radii"] for s in details), default=None)
    duration = 0.0 if first_t is None else last_t - first_t
    supported = math.fsum(s["duration_s"] for s in details)
    return {
        "observation": ("no_supported_intervals" if travel is None else
                        "motion_present" if travel >= THRESHOLD_RADII else "insufficient_motion_evidence"),
        "record_count": len(records), "valid_record_count": valid_count,
        "reference_radius_px": reference_radius, "observed_duration_s": duration,
        "supported_duration_s": supported,
        "supported_time_fraction": supported / duration if duration else None,
        "segment_count": len(segments), "segments": details,
        "max_segment_p02_p98_travel_radii": travel,
        "max_segment_range_radii": max((s["range_radii"] for s in details), default=None),
    }


def report_run(run_dir, manifests):
    source_path = run_dir / "run.json"
    source_bytes = source_path.read_bytes()
    source = json.loads(source_bytes.decode("utf-8"))
    manifest_hashes = [sha256_file(p) for p in manifests]
    fixtures = clips.load_fixtures(manifests)
    # Check every declared clip before opening any track, including clips not yet written.
    for name, role in source["clips"].items():
        if name not in fixtures or clips.development_role(fixtures[name]) != role:
            raise clips.ClipRefused(f"{name}: missing fixture or development-role mismatch")
    tracks = {}
    for method in sorted(source["methods"]):
        if "redetect" in source["methods"][method]:
            raise ValueError("restart runs need explicit identity-segment handling; unsupported in v1")
        if Path(method).name != method or method in (".", ".."):
            raise ValueError("invalid method path")
        folder = run_dir / "candidates" / method
        expected = {f"{name}.jsonl" for name in source["clips"]}
        if any(p.name not in expected for p in folder.iterdir()):
            raise ValueError(f"{folder}: unexpected or incomplete track file")
        tracks[method] = {}
        for name in sorted(source["clips"]):
            if Path(name).name != name or name in (".", ".."):
                raise ValueError("invalid fixture path")
            path = folder / f"{name}.jsonl"
            track_bytes = path.read_bytes()
            records = [json.loads(line) for line in track_bytes.decode("utf-8").splitlines() if line.strip()]
            if not records:
                raise ValueError(f"{path}: empty track")
            if any(r.get("fixture_id") != name or r.get("method") != method for r in records):
                raise ValueError(f"{path}: track identity mismatch")
            tracks[method][name] = {
                "role": source["clips"][name], "source_sha256": hashlib.sha256(track_bytes).hexdigest(),
                "recorded_seed_decision": {k: v for k, v in records[0].get("seed", {}).items()
                                           if k != "candidates"},
                **observe(records),
            }
    if [sha256_file(p) for p in manifests] != manifest_hashes:
        raise ValueError("manifest changed during processing; refusing inconsistent provenance")
    return {
        "rule": RULE, "origin": "machine", "use": "development-observation-only",
        "config": {"threshold_radii": THRESHOLD_RADII, "max_gap_s": MAX_GAP_S,
                   "quantiles": [.02, .98], "weighting": "left_endpoint_valid_interval_duration",
                   "normalization": "first_valid_tracked_radius"},
        "source_run_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "source_sha256": {p.name: sha256_file(p) for p in
                          (Path(__file__), Path(clips.__file__))},
        "manifest_sha256": manifest_hashes,
        "tracks": tracks,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.resolve().is_relative_to(args.run_dir.resolve()):
        parser.error("output must be outside the source run")
    report = report_run(args.run_dir, args.manifest)
    encoded = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(encoded)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
