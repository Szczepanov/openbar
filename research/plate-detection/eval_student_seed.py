#!/usr/bin/env python3
"""Phase B student seed evaluation on development clips (#233 WP3, reproducible form).

For every development clip (``clips.development_clips``; held-out, reserved, pilot and frozen clips
are refused before any media is opened) this decodes the single frame nearest the clip's first
human-labelled truth point, runs a ``DFineStudentProposer`` checkpoint on it, applies the
label-free ``dfine-student-seed-v1`` seed rule with ``sam3-margin-v2`` refusal, and scores the
selected seed against the truth point under ``identity-loose-v1``. Truth is used only to pick the
scored frame and to score; it never steers the seed choice.

Clips are grouped by the fold recorded in a Phase B ``dataset_manifest.json`` (``train``, ``val``,
or ``excluded`` for clips the teacher curation dropped) so that numbers on clips the student was
trained on are never mixed silently with session-disjoint numbers. Output is machine-origin
research evidence and never enters canonical ``Analysis``.

    research/plate-detection/.venv/Scripts/python.exe research/plate-detection/eval_student_seed.py \\
        --repo-root . --checkpoint validation/private/plate-detection-phase-b/student-dfine-s-v1/best_checkpoint \\
        --dataset-dir validation/private/plate-detection-phase-b/dataset-stride15-v1 \\
        --output validation/private/plate-detection-phase-b/student-dfine-s-v1/seed_eval.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Sequence

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "validation" / "tools"))

import clips  # noqa: E402
import label_package  # noqa: E402
import selection  # noqa: E402
import track  # noqa: E402
import truth  # noqa: E402

HARNESS_ID = "plate-detection-phase-b-seed-eval-v1"
FOLDS = ("train", "val", "excluded")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clip_folds(dataset_manifest: dict[str, Any]) -> dict[str, str]:
    """fixture_id -> ``train`` / ``val`` / ``excluded`` from a Phase B dataset manifest."""
    folds: dict[str, str] = {}
    for clip in dataset_manifest.get("clips") or []:
        fid = clip["fixture_id"]
        if clip.get("status") != "included":
            folds[fid] = "excluded"
        elif clip.get("fold") in ("train", "val"):
            folds[fid] = clip["fold"]
        else:
            raise ValueError(f"{fid}: included clip without a train/val fold")
    return folds


def score_seed(rows: Sequence[dict[str, Any]], seed: dict[str, Any], point: truth.TruthPoint) -> dict[str, Any]:
    """Score one seed decision against one truth point (raw box circle and rim-refined circle)."""
    refused = bool(seed["refusal"]["refused"])
    chosen = seed["selected_index"]
    result: dict[str, Any] = {
        "scored": True,
        "selected": chosen is not None,
        "refused": refused,
        "refusal_reason": seed["refusal"].get("reason"),
        "plausible_top1_hit": False,
        "accepted_hit": False,
        "raw_centre_error_rel": None,
        "raw_radius_abs_error_rel": None,
        "refined_centre_error_rel": None,
        "refined_radius_abs_error_rel": None,
    }
    if chosen is None:
        return result
    row = rows[chosen]
    refined = selection.Candidate.from_dict(row)
    hit = selection.match(refined, point.center_x_px, point.center_y_px, point.radius_px)
    result["plausible_top1_hit"] = hit
    result["accepted_hit"] = hit and not refused
    r = point.radius_px
    if hit:
        raw = row.get("raw_box_circle") or row
        result["raw_centre_error_rel"] = round(
            ((raw["center_x_px"] - point.center_x_px) ** 2 + (raw["center_y_px"] - point.center_y_px) ** 2) ** 0.5 / r, 5
        )
        result["raw_radius_abs_error_rel"] = round(abs(raw["radius_px"] - r) / r, 5)
        result["refined_centre_error_rel"] = round(
            ((refined.center_x_px - point.center_x_px) ** 2 + (refined.center_y_px - point.center_y_px) ** 2) ** 0.5
            / r, 5
        )
        result["refined_radius_abs_error_rel"] = round(abs(refined.radius_px - r) / r, 5)
    return result


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 4) if values else None


def summarize(per_clip: dict[str, dict[str, Any]], fold: str | None = None) -> dict[str, Any]:
    """Aggregate per-clip results for one fold (``None`` = every development clip)."""
    chosen = {k: v for k, v in per_clip.items() if fold is None or v["fold"] == fold}
    scored = [v for v in chosen.values() if v["scored"]]
    hits = [v for v in scored if v["plausible_top1_hit"]]

    def errs(key: str) -> list[float]:
        return [v[key] for v in hits if v[key] is not None]

    return {
        "clips": len(chosen),
        "scored_clips": len(scored),
        "plausible_top1_hits": len(hits),
        "refused_scored": sum(1 for v in scored if v["refused"]),
        "accepted_scored": sum(1 for v in scored if not v["refused"]),
        "accepted_hits": sum(1 for v in scored if v["accepted_hit"]),
        "raw_centre_err_median": _median(errs("raw_centre_error_rel")),
        "raw_radius_err_median": _median(errs("raw_radius_abs_error_rel")),
        "refined_centre_err_median": _median(errs("refined_centre_error_rel")),
        "refined_radius_err_median": _median(errs("refined_radius_abs_error_rel")),
    }


def evaluate(*, repo_root: Path, checkpoint: Path, dataset_dir: Path, output: Path,
             manifests: list[Path] | None = None) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    output = output.resolve()
    private = label_package.PRIVATE_ROOT.resolve()
    if not output.is_relative_to(private):
        raise SystemExit(f"output must be under {label_package.PRIVATE_ROOT}")
    if output.exists():
        raise SystemExit(f"{output} already exists; choose a new --output")
    manifest_file = dataset_dir.resolve() / "dataset_manifest.json"
    folds = clip_folds(json.loads(manifest_file.read_text(encoding="utf-8")))
    manifest_paths = manifests or [repo_root / "validation/private/manifest.json",
                                   repo_root / "validation/private/vbt/manifest.json"]
    fixtures = clips.load_fixtures(manifest_paths)
    allowed = clips.development_clips(fixtures)

    import cv2
    import detectors
    import torch

    torch.manual_seed(0)
    detector = detectors.build("dfine_s_student", checkpoint=checkpoint.resolve())
    work = output.parent / f".{output.stem}-frames"
    per_clip: dict[str, dict[str, Any]] = {}
    try:
        for name, role in allowed.items():
            fixture = fixtures[name]
            points = truth.load_truth(name, repo_root / "validation" / "private")
            entry: dict[str, Any] = {"role": role, "fold": folds.get(name, "excluded")}
            if not points:
                per_clip[name] = {**entry, "scored": False, "eval_frame_index": None}
                continue
            media = repo_root / fixture["media"]["repository_path"]
            label_package.require_media_hash(fixture, media)
            probed = label_package.probe(media)
            label_package.require_fixture_probe_match(fixture, probed)
            index = clips._nearest(probed["timestamps_s"])(points[0].timestamp_s)
            frames_dir = work / name
            paths = label_package.extract(media, [index], [probed["pts"][index]], frames_dir, fixture)
            image = cv2.imread(str(paths[0]), cv2.IMREAD_COLOR)
            if image is None:
                raise RuntimeError(f"cannot read {paths[0]}")
            started = time.perf_counter()
            rows, _ = detector.propose_with_masks(image)
            seed = track.choose_seed_student(rows, image.shape[1], image.shape[0])
            latency_ms = round((time.perf_counter() - started) * 1000.0, 2)
            per_clip[name] = {**entry, "eval_frame_index": index, "latency_ms": latency_ms,
                              **score_seed(rows, seed, points[0])}
            shutil.rmtree(frames_dir, ignore_errors=True)
            print(f"{name}: fold={entry['fold']} hit={per_clip[name]['plausible_top1_hit']} "
                  f"refused={per_clip[name]['refused']}", flush=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=HERE, capture_output=True, text=True,
                            check=False).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--", str(HERE)], cwd=HERE, capture_output=True,
                           text=True, check=False).stdout.strip()
    latencies = [v["latency_ms"] for v in per_clip.values() if v.get("latency_ms") is not None]
    record = {
        "harness": HARNESS_ID,
        "origin": "machine",
        "code_commit": commit,
        "dirty": bool(dirty),
        "python": platform.python_version(),
        "detector": detector.config(),
        "checkpoint_files_sha256": {p.name: sha256_file(p) for p in sorted(checkpoint.resolve().glob("*"))
                                    if p.is_file()},
        "dataset_manifest_sha256": sha256_file(manifest_file),
        "seed_rule": track.STUDENT_SEED_RULE,
        "refusal_rule": selection.MARGIN_RULE,
        "match_rule": selection.MATCH_RULE,
        "eval_frame": "decoded frame nearest the first human-labelled truth point",
        "latency_ms_median": _median(latencies),
        "latency_note": "desktop diagnostic only; never substituted for phone evidence",
        "all_dev": summarize(per_clip),
        **{f"{fold}_fold": summarize(per_clip, fold) for fold in FOLDS},
        "per_clip": per_clip,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return record


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo-root", type=Path, required=True)
    p.add_argument("--checkpoint", type=Path, required=True, help="local student checkpoint directory")
    p.add_argument("--dataset-dir", type=Path, required=True, help="Phase B dataset whose manifest assigns folds")
    p.add_argument("--output", type=Path, required=True, help="new JSON file under validation/private")
    p.add_argument("--manifest", type=Path, action="append")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    record = evaluate(repo_root=args.repo_root, checkpoint=args.checkpoint, dataset_dir=args.dataset_dir,
                      output=args.output, manifests=args.manifest)
    print(json.dumps({k: record[k] for k in ("all_dev", "train_fold", "val_fold", "excluded_fold")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
