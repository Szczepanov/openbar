#!/usr/bin/env python3
"""Run Phase A plate proposers on development clips and write raw candidates (#233).

Research only. Reads media through the existing PTS-checked ``label_package`` decoder, runs each
selected method on the planned frames, and writes one JSONL file per (method, clip). Labels are
read only to choose which frames to decode; they never reach a proposer. Frames are deleted after
each clip.

Example (from a worktree, private data in the main checkout):

    python research/plate-detection/run_phase_a.py --repo-root <main checkout> \
        --methods hough gdino_sam2 sam3 --output-dir validation/private/plate-detection-phase-a/run-1
"""
from __future__ import annotations

import argparse
import hashlib
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
import truth  # noqa: E402

HARNESS_ID = "plate-detection-phase-a-v1"
METHODS = ("hough", "gdino_box", "gdino_sam2", "sam3")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checkpoint_files(repo_id: str, revision: str | None = None) -> dict[str, Any]:
    """Revision and weight-file hashes of a locally cached Hugging Face checkpoint or local directory."""
    local_dir = Path(repo_id)
    if local_dir.is_dir():
        snapshot = local_dir.resolve()
        weights = sorted(p for p in snapshot.rglob("*") if p.suffix in (".safetensors", ".bin", ".pt"))
        return {
            "repo_id": snapshot.name,
            "revision": "local",
            "files": {p.relative_to(snapshot).as_posix(): sha256_file(p) for p in weights},
        }
    from huggingface_hub import snapshot_download
    snapshot = Path(snapshot_download(repo_id, revision=revision, local_files_only=True))
    weights = sorted(p for p in snapshot.rglob("*") if p.suffix in (".safetensors", ".bin", ".pt"))
    return {"repo_id": repo_id, "revision": snapshot.name,
            "files": {p.relative_to(snapshot).as_posix(): sha256_file(p) for p in weights}}


def provenance(proposers: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    import detectors
    packages = {}
    for name in ("torch", "transformers", "numpy", "cv2", "PIL", "huggingface_hub"):
        try:
            packages[name] = getattr(__import__(name), "__version__", "unknown")
        except ImportError:
            packages[name] = None
    checkpoints = {}
    for name in proposers:
        repos = {"gdino_box": [detectors.GDINO_CHECKPOINT],
                 "gdino_sam2": [detectors.GDINO_CHECKPOINT, detectors.SAM2_CHECKPOINT],
                 "sam3": [detectors.SAM3_CHECKPOINT]}.get(name, [])
        checkpoints[name] = [checkpoint_files(repo) for repo in repos]
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=HERE, capture_output=True, text=True).stdout.strip()
    return {"harness": HARNESS_ID, "origin": "machine", "code_commit": commit,
            "dirty": bool(subprocess.run(["git", "status", "--porcelain", "--", str(HERE)], cwd=HERE,
                                         capture_output=True, text=True).stdout.strip()),
            "source_sha256": {p.name: sha256_file(p) for p in sorted(HERE.glob("*.py"))},
            "python": platform.python_version(), "packages": packages,
            "methods": {name: p.config() for name, p in proposers.items()}, "checkpoints": checkpoints,
            "frame_plan": {"grid_step_s": clips.GRID_STEP_S, "max_grid_frames": clips.MAX_GRID_FRAMES},
            "repo_root": repo_root.name}


def write_record(path: Path, record: dict[str, Any], allowed: dict[str, str]) -> None:
    """Create run.json, or extend its clip list when a later invocation has identical provenance.

    One run directory holds one configuration: a rerun with other methods, checkpoints, code or
    packages is refused so the record never describes candidates it did not produce.
    """
    clips_so_far: dict[str, str] = {}
    if path.is_file():
        existing = json.loads(path.read_text(encoding="utf-8"))
        clips_so_far = existing.pop("clips")
        if existing != record:
            changed = sorted(k for k in set(existing) | set(record) if existing.get(k) != record.get(k))
            raise SystemExit(f"{path} has different provenance ({', '.join(changed)}); use a new --output-dir")
        conflicts = sorted(k for k in allowed if k in clips_so_far and clips_so_far[k] != allowed[k])
        if conflicts:
            raise SystemExit(f"clip roles changed since the first invocation: {conflicts}")
    path.write_text(json.dumps({**record, "clips": dict(sorted({**clips_so_far, **allowed}.items()))},
                               indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_clip(fixture: dict[str, Any], role: str, repo_root: Path, proposers: dict[str, Any],
             output_dir: Path, max_frames: int | None) -> None:
    import cv2
    media = repo_root / fixture["media"]["repository_path"]
    label_package.require_media_hash(fixture, media)
    probed = label_package.probe(media)
    label_package.require_fixture_probe_match(fixture, probed)
    truth_times = [p.timestamp_s for p in truth.load_truth(fixture["id"], repo_root / "validation" / "private")]
    indices = clips.frame_plan(probed["timestamps_s"], truth_times)
    if max_frames is not None:
        indices = indices[:max_frames]
    frames_dir = output_dir / "frames" / fixture["id"]
    paths = label_package.extract(media, indices, [probed["pts"][i] for i in indices], frames_dir, fixture)
    handles = {}
    try:
        for name in proposers:
            (output_dir / "candidates" / name).mkdir(parents=True, exist_ok=True)
            handles[name] = (output_dir / "candidates" / name / f"{fixture['id']}.jsonl.partial").open("w", encoding="utf-8")
        for index, path in zip(indices, paths):
            image = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if image is None:
                raise RuntimeError(f"cannot read {path}")
            for name, proposer in proposers.items():
                started = time.perf_counter()
                rows = proposer.propose(image)
                elapsed = time.perf_counter() - started
                handles[name].write(json.dumps({
                    "fixture_id": fixture["id"], "role": role, "method": name, "frame_index": index,
                    "pts": probed["pts"][index], "time_s": probed["timestamps_s"][index],
                    "width_px": image.shape[1], "height_px": image.shape[0],
                    "latency_s": round(elapsed, 4), "candidates": rows}, sort_keys=True, allow_nan=False) + "\n")
    finally:
        for handle in handles.values():
            handle.close()
        shutil.rmtree(frames_dir, ignore_errors=True)
    for name in proposers:
        partial = output_dir / "candidates" / name / f"{fixture['id']}.jsonl.partial"
        partial.replace(partial.with_suffix(""))


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo-root", type=Path, required=True, help="checkout holding validation/private media")
    p.add_argument("--manifest", type=Path, action="append",
                   help="defaults to validation/private/manifest.json and validation/private/vbt/manifest.json")
    p.add_argument("--methods", nargs="+", choices=METHODS, default=list(METHODS))
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--clip", action="append", help="restrict to these clip ids (still guarded)")
    p.add_argument("--max-frames", type=int, help="smoke runs only")
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
    if any(name != "hough" for name in args.methods):
        import torch
        torch.manual_seed(0)
    proposers = {name: detectors.build(name) for name in args.methods}
    output_dir.mkdir(parents=True, exist_ok=True)
    write_record(output_dir / "run.json", provenance(proposers, repo_root), allowed)
    for name, role in allowed.items():
        done = all((output_dir / "candidates" / m / f"{name}.jsonl").is_file() for m in proposers)
        if done and not args.force:
            print(f"skip {name} (done)")
            continue
        started = time.perf_counter()
        run_clip(fixtures[name], role, repo_root, proposers, output_dir, args.max_frames)
        print(f"{name}: {time.perf_counter() - started:.1f} s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
