#!/usr/bin/env python3
"""Development-only motion diagnostics (#57, PR #97).

The held-out boundary is deliberately closed here: only a useful development
candidate can justify extending the existing tracker_filter_selection freeze.
"""
from __future__ import annotations

import argparse
from collections import Counter
import platform
from pathlib import Path
import subprocess
import sys

import motion_metrics as metrics
import study_io as io


def named_paths(values: list[str]) -> dict[str, Path]:
    result = {}
    for value in values:
        name, separator, path = value.partition("=")
        if not separator or not name or not path or name in result:
            raise ValueError("candidate must be a unique name=path")
        result[name] = Path(path)
    return result


def environment() -> dict:
    return {"python": platform.python_version(), "platform": platform.platform()}


def source_hashes() -> dict:
    own = {p.name: io.digest(p) for p in sorted(Path(__file__).parent.glob("*.py"))}
    for relative in ("research/opencv-tracking/track.py", "validation/tools/label_package.py",
                     "validation/tools/annotations.py", "validation/tools/schema_check.py"):
        own[relative] = io.digest(io.ROOT / relative)
    return own


def seed_reference_timestamp(seed_doc: dict, reference: dict, annotation: dict) -> float:
    """Resolve the seed frame once; label/prediction matching remains exact."""
    seed = seed_doc["seed"]
    if "frame_index" in seed:
        labelled_frame = [r for r in annotation["samples"] if r.get("frame_index") == seed["frame_index"]]
        if len(labelled_frame) == 1:
            actual = labelled_frame[0]["timestamp_s"]
            if abs(actual - seed["timestamp_s"]) > .0005:
                raise ValueError("seed frame index and annotation timestamp disagree")
            return actual
    matches = [r["timestamp_s"] for r in reference["samples"] if abs(r["timestamp_s"] - seed["timestamp_s"]) <= .0005]
    if len(matches) != 1:
        raise ValueError("seed must identify one baseline observation within 0.0005 s")
    return matches[0]


def snapshot(args) -> dict:
    manifest = io.load(args.manifest)
    io.contract(manifest, "fixture-manifest-v1")
    paths = named_paths(args.input)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=io.ROOT, capture_output=True, text=True, check=True).stdout.strip()
    status = subprocess.run(["git", "status", "--porcelain"], cwd=io.ROOT, capture_output=True, text=True, check=True).stdout.strip()
    metadata = {}
    for name, path in paths.items():
        doc = io.load(path)
        if "fixture_id" in doc:
            item = io.fixture(manifest, doc["fixture_id"])
            io.private_output(item, args.output)
        metadata[name] = {"path": str(path.resolve()), "sha256": io.digest(path)}
        if "implementation" in doc:
            metadata[name]["implementation"] = doc["implementation"]
        if "provenance" in doc:
            metadata[name]["annotation_provenance"] = doc["provenance"]
            metadata[name]["annotation_states"] = dict(Counter(s["annotation_state"] for s in doc["samples"]))
    result = {
        "schema_version": 1, "study_version": io.VERSION, "kind": "development_baseline",
        "commit": commit, "working_tree_dirty": bool(status), "source_sha256": source_hashes(),
        "manifest_sha256": io.digest(args.manifest), "environment": environment(),
        "development_fixture_ids": [f["id"] for f in manifest["fixtures"] if f["purpose"] == "development"],
        "held_out_fixture_ids_not_processed": [f["id"] for f in manifest["fixtures"] if f["purpose"] == "validation"],
        "inputs": metadata,
        "selection_freeze_sha256": io.digest(args.selection_freeze) if args.selection_freeze else None,
        "note": "Baseline inventory only; this is not a production-candidate or held-out freeze.",
    }
    io.write(args.output, result)
    return result


def diagnose(args) -> dict:
    manifest = io.load(args.manifest)
    item = io.fixture(manifest, args.fixture)
    io.private_output(item, args.output_dir)
    annotation = io.annotation(args.annotation, manifest, item)
    seed_doc = io.seed(args.seed, item)
    paths = named_paths(args.candidate)
    if args.baseline not in paths:
        raise ValueError("baseline must be one of the candidate names")
    seed_time = seed_reference_timestamp(seed_doc, io.prediction(paths[args.baseline], item), annotation)
    windows = []
    for value in args.stationary_window:
        start, end = value.split(":")
        windows.append([float(start), float(end)])
    rows, relative = {}, {}
    for name, path in paths.items():
        prediction = io.prediction(path, item)
        rows[name] = {
            "prediction_sha256": io.digest(path), "implementation": prediction["implementation"],
            "all": metrics.evaluate(annotation["samples"], prediction["samples"], max_gap_s=args.max_gap_s,
                                    stationary_windows=windows),
            "seed_excluded": metrics.evaluate(annotation["samples"], prediction["samples"], max_gap_s=args.max_gap_s,
                                              seed_timestamp_s=seed_time, stationary_windows=windows),
            "declared_runtime": prediction.get("runtime"),
        }
    for name, path in named_paths(args.relative).items():
        if name not in paths:
            raise ValueError("relative sidecar must name a prediction candidate")
        sidecar = io.load(path)
        if (sidecar.get("schema_version") != 1 or sidecar.get("study_version") != io.VERSION or
                sidecar.get("fixture_id") != args.fixture or sidecar.get("prediction_sha256") != io.digest(paths[name])):
            raise ValueError("relative sidecar version, identity or prediction hash mismatch")
        predicted = io.prediction(paths[name], item)["samples"]
        all_relative = metrics.relative_diagnostics(annotation["samples"], predicted, sidecar["relative_samples"],
                                                    max_gap_s=args.max_gap_s)
        excluded = metrics.relative_diagnostics(annotation["samples"], predicted, sidecar["relative_samples"],
                                               max_gap_s=args.max_gap_s, seed_timestamp_s=seed_time)
        relative[name] = {"all": all_relative, "seed_excluded": excluded,
                          "paired_delta": {baseline_name: metrics.paired({"matched_errors": [], **excluded}, baseline_row["seed_excluded"])["delta"]
                                           for baseline_name, baseline_row in rows.items()}}
    paired = {name: metrics.paired(row["seed_excluded"], rows[args.baseline]["seed_excluded"])
              for name, row in rows.items() if name != args.baseline}
    result = {"schema_version": 1, "study_version": io.VERSION, "fixture_id": args.fixture,
              "evidence_class": "development_manual_labels", "manifest_sha256": io.digest(args.manifest),
              "annotation_sha256": io.digest(args.annotation), "seed_sha256": io.digest(args.seed),
              "config": {"max_gap_s": args.max_gap_s, "stationary_windows_s": windows,
                         "seed_reference_timestamp_s": seed_time,
                         "matching": "exact_stored_timestamp", "stationary_label_range_max_px": .5},
              "baseline": args.baseline, "candidates": rows, "paired": paired, "relative": relative,
              "notes": ["Sparse label intervals are not adjacent frames.",
                        "Manual labels and same-video markers are not independent physical truth.",
                        "No canonical benchmark or kinematic gate is changed by these diagnostics."]}
    if args.canonical:
        canonical_outputs(args, item, paths, annotation, seed_time)
    io.write(args.output_dir / "motion-diagnostics.json", result)
    lines = [f"# Development motion diagnostics: {args.fixture}", "",
             "Manual-label evidence; seed excluded. Delta errors use actual labelled intervals.", "",
             "| Candidate | Matched labels | Centre MAE px | Delta MAE px | Available intervals |", "| --- | ---: | ---: | ---: | ---: |"]
    def display(value):
        return "unsupported" if value is None else f"{value:.4f}"
    for name, row in rows.items():
        m = row["seed_excluded"]
        lines.append(f"| {name} | {m['matched_tracked_samples']}/{m['labelled_samples']} | {display(m['center_error_px']['mae'])} | "
                     f"{display(m['delta_position_error_px']['mae'])} | {m['available_intervals']} |")
    lines += ["", "Candidate comparisons use common support; see JSON for paired distributions, interval lengths, loss and confidence.",
              "Stationary jitter is unsupported unless explicitly requested with dense stationary labels.",
              "Held-out selection and physical accuracy remain unevaluated.", ""]
    (args.output_dir / "motion-diagnostics.md").write_bytes("\n".join(lines).encode())
    return result


def canonical_outputs(args, item, paths, annotation, start_s):
    """Delegate existing measurement metrics to the authoritative CLI unchanged."""
    end_s = annotation["samples"][-1]["timestamp_s"]
    cases = [{"id": f"{args.fixture}-{index}", "fixture_id": args.fixture,
              "fixture_manifest": str(args.manifest.resolve()), "annotations": str(args.annotation.resolve()),
              "manual_seed": str(args.seed.resolve()), "predictions": str(path.resolve()),
              "selected_range_s": {"start_s": start_s, "end_s": end_s},
              "timestamp_tolerance_s": .0005, "min_confidence": 0}
             for index, path in enumerate(paths.values())]
    suite = args.output_dir / "canonical.benchmark-v1.json"
    document = {"schema_version": 1, "pipeline_version": "m0-benchmark-v1", "cases": cases}
    io.contract(document, "benchmark-suite-v1")
    io.write(suite, document)
    cli = ["cargo", "run", "--locked", "-q", "-p", "openbar-cli", "--"]
    subprocess.run([*cli, "benchmark", "--suite", str(suite), "--output", str(args.output_dir / "canonical.benchmark-result-v1.json")],
                   check=True, cwd=io.ROOT)
    media = io.media_path(item, args.repository_root)
    for index, path in enumerate(paths.values()):
        observation_end_s = io.prediction(path, item)["samples"][-1]["timestamp_s"]
        subprocess.run([*cli, "analyze", "--manifest", str(args.manifest.resolve()), "--fixture", args.fixture,
                        "--video", str(media), "--start-s", str(max(0., start_s - .000001)),
                        "--end-s", str(max(end_s, observation_end_s) + .000001),
                        "--seed", str(args.seed.resolve()), "--observations", str(path.resolve()),
                        "--plate-diameter-m", str(item["load"]["plate_diameter_m"]), "--filter", "raw",
                        "--kinematics-max-gap-s", str(args.max_gap_s), "--kinematics-min-confidence", "0",
                        "--output", str(args.output_dir / f"canonical-{index}.analysis-v1.json")], check=True, cwd=io.ROOT)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest="command", required=True)
    snap = commands.add_parser("snapshot", help="Record baseline inputs without consuming held-out media/labels")
    snap.add_argument("--manifest", type=Path, required=True)
    snap.add_argument("--input", action="append", default=[], metavar="NAME=PATH")
    snap.add_argument("--selection-freeze", type=Path)
    snap.add_argument("--output", type=Path, required=True)
    sub = commands.add_parser("diagnose")
    sub.add_argument("--manifest", type=Path, required=True)
    sub.add_argument("--fixture", required=True)
    sub.add_argument("--seed", type=Path, required=True)
    sub.add_argument("--output-dir", type=Path, required=True)
    sub.add_argument("--repository-root", type=Path, default=io.ROOT)
    sub.add_argument("--annotation", type=Path, required=True)
    sub.add_argument("--candidate", action="append", required=True, metavar="NAME=PATH")
    sub.add_argument("--baseline", required=True)
    sub.add_argument("--relative", action="append", default=[], metavar="NAME=SIDECAR")
    sub.add_argument("--max-gap-s", type=float, default=.2)
    sub.add_argument("--stationary-window", action="append", default=[], metavar="START_S:END_S")
    sub.add_argument("--canonical", action="store_true", help="Also run authoritative Rust benchmark and analyze --observations")
    return p


def main(argv=None) -> int:
    try:
        args = parser().parse_args(argv)
        {"snapshot": snapshot, "diagnose": diagnose}[args.command](args)
        print(f"{args.command}: report written")
        return 0
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        print(f"bar-path experiment: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
