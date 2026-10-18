#!/usr/bin/env python3
"""Run Phase A.2 (#233): detect the plate once on frame 0, then track it with SAM 2.1 video.

Research only, development clips only (``clips.development_role``). Frame 0 is the label-free
decision frame: GroundingDINO + SAM 2.1 propose candidates there, the plausible top-1 is chosen
(``track.choose_seed``), and its mask is propagated through every ``--stride``-th decoded frame
plus each labelled frame (labels add frames so they can be scored; they never steer the tracker).
Writes per-frame JSONL in the Phase A format under ``candidates/track_sam2/``; frame 0 also
carries the seed candidates, the selected index and the ``close-score-v1`` refusal outcome.

``--tracker edgetam`` swaps SAM 2.1 video tracking for EdgeTAM (#57) and writes under
``candidates/track_edgetam[_sam3seed]/``.

``--seed sam3`` swaps the seed for SAM 3 (``track.choose_seed_sam3``: size window, ``in-frame-v1``,
``sam3-margin-v2`` refusal) and writes under ``candidates/track_sam2_sam3seed/``; tracking is unchanged.
Frames are deleted after each clip.

    python research/plate-detection/run_phase_a2.py --repo-root <main checkout> \
        --output-dir validation/private/plate-detection-phase-a2/run-1
"""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "validation" / "tools"))

import clips  # noqa: E402
import label_package  # noqa: E402
import run_phase_a  # noqa: E402
import selection  # noqa: E402
import track  # noqa: E402
import truth  # noqa: E402

HARNESS_ID = "plate-detection-phase-a2-v1"
# Seed variants: seed rule and the refusal it records (the output method comes from track.method_name).
SEEDS = {
    "gdino_sam2": {"choose": track.choose_seed, "seed_rule": "plausible_top1",
                   "refusal": {"rule": selection.REFUSAL_RULE, "score_ratio": selection.REFUSAL_SCORE_RATIO}},
    "sam3": {"choose": track.choose_seed_sam3,
             "seed_rule": {"rule": track.SAM3_SEED_RULE, "in_frame_rule": selection.IN_FRAME_RULE,
                           "size_window_fraction": [selection.MIN_RADIUS_FRACTION, selection.MAX_RADIUS_FRACTION],
                           "requires_mask": True},
             "refusal": {"rule": selection.MARGIN_RULE, "margin_score": selection.MARGIN_SCORE,
                         "concentric_centre_fraction": selection.SAME_SPOT_CENTRE_FRACTION,
                         "score_decimals": selection.SCORE_DECIMALS,
                         "also_recorded": {"rule": selection.REFUSAL_RULE,
                                           "score_ratio": selection.REFUSAL_SCORE_RATIO}}},
}


def provenance(seed: str, detector: Any, tracker: Any, stride: int, repo_root: Path) -> dict[str, Any]:
    import detectors
    from importlib import metadata
    # Installed distribution versions, read without importing the libraries. Keys follow Phase A plus
    # torchvision and timm; values are distribution versions (cv2 reads 4.12.0.88 where Phase A's
    # cv2.__version__ read 4.12.0).
    opencv = ("opencv-python-headless", "opencv-python", "opencv-contrib-python-headless", "opencv-contrib-python")
    distributions = {"torch": "torch", "torchvision": "torchvision", "transformers": "transformers", "timm": "timm",
                     "numpy": "numpy", "cv2": opencv, "PIL": "pillow", "huggingface_hub": "huggingface_hub"}
    packages = {}
    for name, dists in distributions.items():
        packages[name] = None
        for dist in (dists,) if isinstance(dists, str) else dists:
            try:
                packages[name] = metadata.version(dist)
                break
            except metadata.PackageNotFoundError:
                continue
    torch = sys.modules.get("torch")  # imported by the tracker; never imported here just to describe it
    runtime = ({"cuda": torch.version.cuda, "device": torch.cuda.get_device_name(0) if torch.cuda.is_available()
                else "cpu"} if torch is not None else None)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=HERE, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--", str(HERE)], cwd=HERE, capture_output=True,
                           text=True).stdout.strip()
    variant = SEEDS[seed]
    name = track.method_name(tracker.name, seed)
    method = {"seed_detector": {"method": seed, **detector.config()},
              "seed_frame": "first_decoded_frame", "seed_rule": variant["seed_rule"], "refusal": variant["refusal"],
              "tracker": tracker.config(), "track_stride": stride,
              "circle_fit": "geometry.circle_from_mask (seed rows may fall back to geometry.circle_from_box)"}
    seed_repos = ({detectors.SAM3_CHECKPOINT} if seed == "sam3"
                  else {detectors.GDINO_CHECKPOINT, detectors.SAM2_CHECKPOINT})
    pinned = {track.TRACKERS[tracker.name]["checkpoint"]: track.TRACKERS[tracker.name]["revision"]}
    repos = sorted(seed_repos | set(pinned))
    return {"harness": HARNESS_ID, "origin": "machine", "code_commit": commit, "dirty": bool(dirty),
            "source_sha256": {p.name: run_phase_a.sha256_file(p) for p in sorted(HERE.glob("*.py"))},
            "python": platform.python_version(), "packages": packages, "runtime": runtime, "methods": {name: method},
            "checkpoints": {name: [run_phase_a.checkpoint_files(repo, pinned.get(repo)) for repo in repos]},
            "repo_root": repo_root.name}


def run_clip(fixture: dict[str, Any], role: str, repo_root: Path, detector: Any, tracker: Any,
             stride: int, output_dir: Path, seed_name: str = "gdino_sam2") -> dict[str, Any]:
    variant = SEEDS[seed_name]
    method = track.method_name(tracker.name, seed_name)
    import cv2
    import geometry
    media = repo_root / fixture["media"]["repository_path"]
    label_package.require_media_hash(fixture, media)
    probed = label_package.probe(media)
    label_package.require_fixture_probe_match(fixture, probed)
    truth_times = [p.timestamp_s for p in truth.load_truth(fixture["id"], repo_root / "validation" / "private")]
    indices = clips.track_plan(probed["timestamps_s"], truth_times, stride)
    frames_dir = output_dir / "frames" / fixture["id"]
    paths = label_package.extract(media, indices, [probed["pts"][i] for i in indices], frames_dir, fixture)
    out_path = output_dir / "candidates" / method / f"{fixture['id']}.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    partial = out_path.with_suffix(".jsonl.partial")
    summary = {"frames": len(indices), "lost": 0}
    try:
        with partial.open("w", encoding="utf-8") as handle:
            seed_mask = None
            for position, (index, path) in enumerate(zip(indices, paths)):
                image = cv2.imread(str(path), cv2.IMREAD_COLOR)
                if image is None:
                    raise RuntimeError(f"cannot read {path}")
                started = time.perf_counter()
                extra: dict[str, Any] = {}
                if position == 0:
                    rows, masks = detector.propose_with_masks(image)
                    seed = variant["choose"](rows, image.shape[1], image.shape[0])
                    chosen = seed["selected_index"]
                    seed_mask = None if chosen is None else masks[chosen]
                    if seed_mask is not None:
                        tracker.start(seed_mask)
                    extra["seed"] = {"candidates": rows, **seed, "tracked": seed_mask is not None}
                    summary["seed"] = {k: v for k, v in extra["seed"].items() if k != "candidates"}
                candidates = []
                if seed_mask is not None:
                    mask, logit = tracker.step(image)
                    row = track.tracked_row(geometry.circle_from_mask(mask), logit)
                    candidates = [] if row is None else [row]
                summary["lost"] += not candidates
                handle.write(json.dumps({
                    "fixture_id": fixture["id"], "role": role, "method": method, "frame_index": index,
                    "pts": probed["pts"][index], "time_s": probed["timestamps_s"][index],
                    "width_px": image.shape[1], "height_px": image.shape[0],
                    "latency_s": round(time.perf_counter() - started, 4), "candidates": candidates, **extra},
                    sort_keys=True, allow_nan=False) + "\n")
    finally:
        shutil.rmtree(frames_dir, ignore_errors=True)
    partial.replace(out_path)
    return summary


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo-root", type=Path, required=True, help="checkout holding validation/private media")
    p.add_argument("--manifest", type=Path, action="append",
                   help="defaults to validation/private/manifest.json and validation/private/vbt/manifest.json")
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--stride", type=int, default=clips.TRACK_STRIDE)
    p.add_argument("--seed", choices=sorted(SEEDS), default="gdino_sam2", help="seed detector and rule")
    p.add_argument("--tracker", choices=sorted(track.TRACKERS), default="sam2", help="video tracker (#57)")
    p.add_argument("--clip", action="append", help="restrict to these clip ids (still guarded)")
    p.add_argument("--force", action="store_true", help="rerun clips that already have output")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    repo_root = args.repo_root.resolve()
    output_dir = args.output_dir.resolve()
    if not output_dir.is_relative_to(label_package.PRIVATE_ROOT.resolve()):
        raise SystemExit(f"output must be under {label_package.PRIVATE_ROOT}")
    manifests = args.manifest or [repo_root / "validation/private/manifest.json",
                                  repo_root / "validation/private/vbt/manifest.json"]
    fixtures = clips.load_fixtures(manifests)
    allowed = clips.development_clips(fixtures)
    if args.clip:
        for name in args.clip:
            if name not in fixtures:
                raise SystemExit(f"unknown clip id {name!r}")
            clips.development_role(fixtures[name])  # refuses loudly
        allowed = {name: allowed[name] for name in args.clip}
    import detectors
    import torch
    torch.manual_seed(0)
    detector = detectors.build(args.seed)
    tracker = track.Sam2VideoTracker(args.tracker)
    output_dir.mkdir(parents=True, exist_ok=True)
    run_phase_a.write_record(output_dir / "run.json", provenance(args.seed, detector, tracker, args.stride, repo_root), allowed)
    for name, role in allowed.items():
        if (output_dir / "candidates" / track.method_name(args.tracker, args.seed) / f"{name}.jsonl").is_file() \
                and not args.force:
            print(f"skip {name} (done)")
            continue
        started = time.perf_counter()
        summary = run_clip(fixtures[name], role, repo_root, detector, tracker, args.stride, output_dir, args.seed)
        print(f"{name}: {time.perf_counter() - started:.1f} s {json.dumps(summary, sort_keys=True)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
