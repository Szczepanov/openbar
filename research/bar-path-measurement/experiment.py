#!/usr/bin/env python3
"""Development-only bar-path metrology runner (#57, PR #97).

The held-out boundary is deliberately closed here: only a useful development
candidate can justify extending the existing tracker_filter_selection freeze.
"""
from __future__ import annotations

import argparse
from collections import Counter
import os
import platform
from pathlib import Path
import subprocess
import sys
import time

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


def repository_state() -> dict:
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=io.ROOT,
                            capture_output=True, text=True, check=True).stdout.strip()
    status = subprocess.run(["git", "status", "--porcelain"], cwd=io.ROOT,
                            capture_output=True, text=True, check=True).stdout.strip()
    return {"commit": commit, "working_tree_dirty": bool(status)}


def decoder_environment() -> dict:
    result = {}
    for name in ("ffmpeg", "ffprobe"):
        version = subprocess.run([name, "-version"], capture_output=True, text=True, check=True).stdout.splitlines()
        result[name] = version[0] if version else "unknown"
    result["thread_environment"] = {name: os.environ.get(name) for name in
                                    ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")}
    return result


def validated_decode_frames(decoder, media: Path, width: int, height: int, count: int):
    """Translate the shared decoder's explicit failure into this command's fail-closed contract."""
    try:
        yield from decoder.decode_frames(media, width, height, count)
    except decoder.SpikeError as error:
        raise ValueError(f"decoder: {error}") from error


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
        **repository_state(), "source_sha256": source_hashes(),
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


def run(args) -> dict:
    # Keep diagnostics/snapshot stdlib-only; image dependencies are research-only.
    import cv2
    import numpy as np
    import fusion
    import vision
    sys.path.insert(0, str(io.ROOT / "research" / "opencv-tracking"))
    import track as decoder
    import label_package

    manifest = io.load(args.manifest)
    item = io.fixture(manifest, args.fixture)  # Refuse held-out before any label/media access.
    io.private_output(item, args.output_dir)
    seed_doc = io.seed(args.seed, item)
    coarse = io.prediction(args.coarse, item)
    supplied = io.load(args.config) if args.config else {}
    if set(supplied) - {"absolute", "registration", "fusion", "patch_radius_factor"}:
        raise ValueError("unknown experiment configuration")
    absolute_config = vision._config(vision.RADIAL_CONFIG if args.method == "radial" else vision.MARKER_CONFIG,
                                     supplied.get("absolute"))
    registration_config = vision._config(vision.REGISTRATION_CONFIG, supplied.get("registration"))
    fusion_config = fusion._config(supplied.get("fusion", {}))
    patch_factor = io.number(supplied.get("patch_radius_factor", 1.5), "patch_radius_factor", 1.25)
    if patch_factor > 3:
        raise ValueError("patch_radius_factor must be <= 3")
    config = {"absolute": absolute_config, "registration": registration_config,
              "fusion": fusion_config, "patch_radius_factor": patch_factor}
    media = io.media_path(item, args.repository_root)
    probed = label_package.probe(media)
    label_package.require_fixture_probe_match(item, probed)
    width, height = io.size(item)
    timestamps = probed["timestamps_s"]
    rounded = [round(t, 6) for t in timestamps]
    if len(set(rounded)) != len(rounded):
        raise ValueError("six-decimal timestamp precision would merge decoded frames")
    coarse_by_time = {r["timestamp_s"]: r for r in coarse["samples"]}
    if set(coarse_by_time) - set(rounded):
        raise ValueError("coarse timestamps must exactly match decoded timestamps at six decimals")
    seed = seed_doc["seed"]
    seed_index = min(range(len(timestamps)), key=lambda i: abs(timestamps[i] - seed["timestamp_s"]))
    if abs(timestamps[seed_index] - seed["timestamp_s"]) > .0005:
        raise ValueError("seed has no matching decoded frame")
    if "frame_index" in seed and seed["frame_index"] != seed_index:
        raise ValueError("seed timestamp/frame index mismatch")
    if rounded[seed_index] not in coarse_by_time:
        raise ValueError("coarse observations must include the seed frame")
    radius = seed["target"]["radius_px"]
    seed_anchor_weight = seed.get("selection_confidence", 0.)
    cv2.setNumThreads(1)
    cv2.setRNGSeed(0)
    absolute, relative, diagnostics = [], [], []
    previous_patch = previous_origin = previous_time = None
    elapsed_s = 0.
    start = time.perf_counter()
    for index, frame in enumerate(validated_decode_frames(decoder, media, width, height, len(timestamps))):
        t = rounded[index]
        if index < seed_index or t > coarse["samples"][-1]["timestamp_s"]:
            continue  # Still drain all decoded frames to enforce count/process status.
        began = time.perf_counter()
        raw = coarse_by_time.get(t)
        sample = {"timestamp_s": t, "state": "lost"}
        patch = origin = None
        absolute_result = {"diagnostics": {"loss_reason": "coarse_loss"}, "center_px": None}
        if raw and raw["state"] == "tracked":
            center = tuple(raw["center_px"][a] for a in metrics.AXES)
            absolute_result = (vision.radial_center if args.method == "radial" else vision.marker_center)(frame, center, radius, absolute_config)
            if absolute_result["center_px"] is not None:
                sample.update(state="tracked", center_px=absolute_result["center_px"], confidence=absolute_result["confidence"])
            half = int(np.ceil(radius * patch_factor))
            x, y = (round(center[0]) - half, round(center[1]) - half)
            side = 2 * half + 1
            if x >= 0 and y >= 0 and x + side <= width and y + side <= height:
                patch = cv2.cvtColor(frame[y:y + side, x:x + side], cv2.COLOR_BGR2GRAY)
                origin = (x, y)
        if index == seed_index:
            # The emitted tracker confidence describes the deterministic initialization observation.
            # Human selection confidence remains separate and is only used as the optional seed-anchor weight.
            sample = {"timestamp_s": t, "state": "tracked", "center_px": dict(seed["target"]["center"]),
                      "confidence": 1.0}
            absolute_result["diagnostics"]["manual_seed_anchor"] = True
            if "selection_confidence" in seed:
                absolute_result["diagnostics"]["manual_seed_selection_confidence"] = seed["selection_confidence"]
        if previous_time is not None:
            result = {"delta_px": None, "confidence": None, "diagnostics": {"loss_reason": "patch_unavailable"}}
            if patch is not None and previous_patch is not None and t - previous_time <= fusion_config["max_gap_s"]:
                result = vision.relative_shift(previous_patch, patch, registration_config)
                if result["delta_px"] is not None:
                    result["delta_px"] = {a: result["delta_px"][a] + origin[i] - previous_origin[i]
                                          for i, a in enumerate(metrics.AXES)}
            relative.append({"timestamp_s": t, "previous_timestamp_s": previous_time, **result})
        diagnostics.append({"timestamp_s": t, "frame_index": index, **absolute_result["diagnostics"]})
        absolute.append(sample)
        previous_patch, previous_origin, previous_time = patch, origin, t
        elapsed_s += time.perf_counter() - began
    if not absolute:
        raise ValueError("no frames in selected coarse/seed range")
    # Preserve the historical optional seed-anchor weighting without publishing human annotation
    # confidence as tracker confidence in tracker-prediction-v1.
    fusion_absolute = [dict(sample) for sample in absolute]
    fusion_absolute[0]["confidence"] = seed_anchor_weight
    fused = fusion.fuse(fusion_absolute, [{k: r[k] for k in ("timestamp_s", "previous_timestamp_s", "delta_px", "confidence")} for r in relative], fusion_config)
    if fused[0]["state"] == "tracked":
        fused[0]["confidence"] = 1.0
    io.samples(absolute, width, height)
    io.samples(fused, width, height)
    common = {"schema_version": 1, "fixture_id": args.fixture, "coordinate_space": "decoded_display_pixels",
              "source_video_sha256": item["media"]["sha256"]}
    provenance = {"study_version": io.VERSION, **repository_state(),
                  "manifest_sha256": io.digest(args.manifest),
                  "seed_sha256": io.digest(args.seed), "coarse_prediction_sha256": io.digest(args.coarse),
                  "coarse_implementation": coarse["implementation"], "source_sha256": source_hashes(),
                  "environment": {**environment(), **decoder_environment(), "numpy": np.__version__, "opencv": cv2.__version__},
                  "config": config, "seed_timestamp_s": rounded[seed_index], "timestamp_decimals": 6,
                  "seed_selection_confidence": seed.get("selection_confidence"),
                  "fusion_seed_anchor_weight": seed_anchor_weight,
                  "end_s": absolute[-1]["timestamp_s"], "confidence": "algorithm_specific_not_probability"}
    for name, rows in ((args.method, absolute), (args.method + "-fused", fused)):
        doc = {**common, "implementation": {"name": "research-" + name, "version": "1", "config": provenance}, "samples": rows}
        io.contract(doc, "tracker-prediction-v1")
        output = args.output_dir / f"{name}.prediction-v1.json"
        io.write(output, doc)
        io.write(args.output_dir / f"{name}.sidecar.json", {
            "schema_version": 1, "study_version": io.VERSION, "fixture_id": args.fixture,
            "prediction_sha256": io.digest(output), "provenance": provenance,
            "absolute_samples": absolute, "absolute_diagnostics": diagnostics, "relative_samples": relative})
    timing = {"schema_version": 1, "study_version": io.VERSION, "fixture_id": args.fixture,
              "processing_wall_s": time.perf_counter() - start, "measurement_compute_s": elapsed_s,
              "frames": len(absolute), "environment": provenance["environment"],
              "note": "Desktop diagnostic only; includes full decoder drain, excludes coarse producer cost. Not Pixel 8 runtime."}
    io.write(args.output_dir / "runtime.json", timing)
    return {"frames": len(absolute), "absolute_tracked": sum(s["state"] == "tracked" for s in absolute),
            "relative_measured": sum(r["delta_px"] is not None for r in relative),
            "absolute_loss_reasons": dict(Counter(d.get("loss_reason") for d in diagnostics if d.get("loss_reason"))),
            "relative_loss_reasons": dict(Counter(r["diagnostics"].get("loss_reason") for r in relative if r["delta_px"] is None))}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest="command", required=True)
    snap = commands.add_parser("snapshot", help="Record baseline inputs without consuming held-out media/labels")
    snap.add_argument("--manifest", type=Path, required=True)
    snap.add_argument("--input", action="append", default=[], metavar="NAME=PATH")
    snap.add_argument("--selection-freeze", type=Path)
    snap.add_argument("--output", type=Path, required=True)

    diagnose_parser = commands.add_parser("diagnose")
    diagnose_parser.add_argument("--manifest", type=Path, required=True)
    diagnose_parser.add_argument("--fixture", required=True)
    diagnose_parser.add_argument("--seed", type=Path, required=True)
    diagnose_parser.add_argument("--output-dir", type=Path, required=True)
    diagnose_parser.add_argument("--repository-root", type=Path, default=io.ROOT)
    diagnose_parser.add_argument("--annotation", type=Path, required=True)
    diagnose_parser.add_argument("--candidate", action="append", required=True, metavar="NAME=PATH")
    diagnose_parser.add_argument("--baseline", required=True)
    diagnose_parser.add_argument("--relative", action="append", default=[], metavar="NAME=SIDECAR")
    diagnose_parser.add_argument("--max-gap-s", type=float, default=.2)
    diagnose_parser.add_argument("--stationary-window", action="append", default=[], metavar="START_S:END_S")
    diagnose_parser.add_argument("--canonical", action="store_true", help="Also run authoritative Rust benchmark and analyze --observations")

    run_parser = commands.add_parser("run")
    run_parser.add_argument("--manifest", type=Path, required=True)
    run_parser.add_argument("--fixture", required=True)
    run_parser.add_argument("--seed", type=Path, required=True)
    run_parser.add_argument("--output-dir", type=Path, required=True)
    run_parser.add_argument("--repository-root", type=Path, default=io.ROOT)
    run_parser.add_argument("--coarse", type=Path, required=True)
    run_parser.add_argument("--method", choices=("radial", "marker"), default="radial")
    run_parser.add_argument("--config", type=Path)
    return p


def main(argv=None) -> int:
    try:
        args = parser().parse_args(argv)
        result = {"snapshot": snapshot, "diagnose": diagnose, "run": run}[args.command](args)
        if args.command == "run":
            print(result)
        else:
            print(f"{args.command}: report written")
        return 0
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        print(f"bar-path experiment: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
