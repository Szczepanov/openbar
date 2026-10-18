#!/usr/bin/env python3
"""Curate a leak-free, session-grouped COCO dataset from SAM 3 + SAM 2.1 teacher tracks (#233 Phase B).

Stdlib only, model-free. Reads guarded (or raw, automatically applying ``track-guard-v2``) teacher
JSONL tracks on the 27 development clips (``clips.development_role``), vetoes clips whose seed was
refused or disagrees with development truth (``identity-loose-v1``), groups clips by recording
session so no session straddles ``train`` and ``val``, and writes deterministic COCO bounding-box
annotations plus ``dataset_manifest.json``.

Clips tagged ``split-reserved-57``, ``purpose: validation``, ``split-frozen-eval``, pilot, or
refusal-candidate are unconditionally refused by ``clips.development_role`` and ``held_out_guard``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "validation" / "tools"))

import clips  # noqa: E402
import guard  # noqa: E402
import held_out_guard  # noqa: E402
import label_package  # noqa: E402
import selection  # noqa: E402
import truth  # noqa: E402

HARNESS_ID = "plate-detection-phase-b-distill-v1"
CATEGORY_ID = 1
CATEGORY_NAME = "barbell_plate"
TRUTH_MATCH_TOLERANCE_S = 0.04
DEFAULT_VAL_SESSIONS = frozenset({
    "session-self-2023-02-06",
    "session-self-2023-02-12",
    "session-self-early",
})
_DATE_TOKEN_RE = re.compile(r"-(1015|0203|0206|0212|0213|0215|0219|0226)-")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def session_group(fixture_id: str) -> str:
    """Return the recording-session group identifier so no session straddles train and val."""
    if fixture_id.startswith("vbt-"):
        return "session-vbt-2026-10-03"
    match = _DATE_TOKEN_RE.search(fixture_id)
    if match:
        token = match.group(1)
        year = "2026" if token == "1015" else "2023"
        month, day = token[:2], token[2:]
        return f"session-self-{year}-{month}-{day}"
    if fixture_id.startswith("self-"):
        return "session-self-early"
    return f"session-other-{fixture_id}"


def assign_session_folds(
    clip_ids: list[str],
    val_sessions: frozenset[str] | set[str] = DEFAULT_VAL_SESSIONS,
) -> dict[str, str]:
    """Assign each clip_id to 'train' or 'val' strictly by session_group."""
    folds: dict[str, str] = {}
    for clip_id in sorted(clip_ids):
        group = session_group(clip_id)
        folds[clip_id] = "val" if group in val_sessions else "train"
    train_groups = {session_group(cid) for cid, fold in folds.items() if fold == "train"}
    val_groups = {session_group(cid) for cid, fold in folds.items() if fold == "val"}
    overlap = train_groups & val_groups
    if overlap:
        raise RuntimeError(f"session group leakage between train and val: {sorted(overlap)}")
    return folds


def circle_to_coco_bbox(
    center_x_px: float,
    center_y_px: float,
    radius_px: float,
    width_px: int,
    height_px: int,
) -> list[float] | None:
    """Convert an ADR-0007 pixel-centre circle (cx, cy, r) to a clamped pixel-edge COCO
    [x_min, y_min, width, height] box (exact inverse of ``geometry.circle_from_box`` when unclipped)."""
    if not all(math.isfinite(v) for v in (center_x_px, center_y_px, radius_px)):
        return None
    if radius_px <= 0.0 or width_px <= 0 or height_px <= 0:
        return None
    edge_cx = center_x_px + 0.5
    edge_cy = center_y_px + 0.5
    x0 = max(0.0, min(float(width_px), edge_cx - radius_px))
    y0 = max(0.0, min(float(height_px), edge_cy - radius_px))
    x1 = max(0.0, min(float(width_px), edge_cx + radius_px))
    y1 = max(0.0, min(float(height_px), edge_cy + radius_px))
    box_w = round(x1 - x0, 4)
    box_h = round(y1 - y0, 4)
    if box_w <= 0.0 or box_h <= 0.0:
        return None
    return [round(x0, 4), round(y0, 4), box_w, box_h]


def seed_matches_truth(
    records: list[dict[str, Any]],
    truth_points: list[truth.TruthPoint],
) -> tuple[bool, str]:
    """Verify that the teacher clip's seed was accepted and agrees with development truth."""
    if not records:
        return False, "empty_records"
    first_seed = records[0].get("seed") or {}
    refusal = first_seed.get("refusal")
    if first_seed.get("refused") is True or (
        isinstance(refusal, dict) and refusal.get("refused") is True
    ):
        return False, "seed_refused"
    if first_seed.get("tracked") is False or not records[0].get("candidates"):
        return False, "seed_untracked"

    if not truth_points:
        return True, "accepted_unlabelled_exploration"

    # Check the earliest aligned truth label for seed acquisition and every subsequent aligned truth
    # label where the guarded track still reports a candidate so mid-clip drift is vetoed.
    verified_first = False
    for point in sorted(truth_points, key=lambda p: p.timestamp_s):
        nearest = min(records, key=lambda r: abs(float(r["time_s"]) - point.timestamp_s))
        if abs(float(nearest["time_s"]) - point.timestamp_s) > TRUTH_MATCH_TOLERANCE_S:
            continue
        candidates = nearest.get("candidates") or []
        if not verified_first:
            if not candidates:
                return False, "seed_lost_at_first_truth"
            cand = selection.Candidate.from_dict(candidates[0])
            if not selection.match(cand, point.center_x_px, point.center_y_px, point.radius_px):
                return False, "seed_off_target_vs_truth"
            verified_first = True
        elif candidates:
            cand = selection.Candidate.from_dict(candidates[0])
            if not selection.match(cand, point.center_x_px, point.center_y_px, point.radius_px):
                return False, "track_off_target_vs_truth"

    if not verified_first:
        return False, "no_aligned_truth_frame"
    return True, "accepted_verified_vs_truth"


def curate_clip_records(
    fixture: dict[str, Any],
    records: list[dict[str, Any]],
    truth_points: list[truth.TruthPoint],
    *,
    frame_stride: int = 1,
) -> dict[str, Any]:
    """Validate clip split role, apply track-guard-v2 if needed, and extract positive boxes."""
    if frame_stride < 1:
        raise ValueError("frame_stride must be at least 1")
    role = clips.development_role(fixture)
    fixture_id = fixture["id"]

    ordered = sorted(records, key=lambda r: float(r["time_s"]))
    guarded = ordered if (ordered and "guard" in ordered[0]) else guard.guard_track(ordered)
    accepted_seed, reason = seed_matches_truth(guarded, truth_points)
    if not accepted_seed:
        return {
            "fixture_id": fixture_id,
            "role": role,
            "session_group": session_group(fixture_id),
            "status": "excluded",
            "reason": reason,
            "total_frames": len(guarded),
            "accepted_frames": 0,
            "frames": [],
        }

    frames: list[dict[str, Any]] = []
    for rec in guarded:
        guard_info = rec.get("guard") or {"status": "tracked", "session_index": 0}
        if guard_info.get("status") != "tracked" or int(guard_info.get("session_index", 0)) != 0:
            continue
        candidates = rec.get("candidates") or []
        if not candidates:
            continue
        cand = candidates[0]
        cx = float(cand["center_x_px"])
        cy = float(cand["center_y_px"])
        radius = float(cand["radius_px"])
        width_px = int(rec["width_px"])
        height_px = int(rec["height_px"])
        bbox = circle_to_coco_bbox(cx, cy, radius, width_px, height_px)
        if bbox is None:
            continue
        frames.append(
            {
                "fixture_id": fixture_id,
                "frame_index": int(rec["frame_index"]),
                "pts": int(rec["pts"]),
                "time_s": float(rec["time_s"]),
                "width_px": width_px,
                "height_px": height_px,
                "bbox": bbox,
                "center_x_px": round(cx, 4),
                "center_y_px": round(cy, 4),
                "radius_px": round(radius, 4),
                "teacher_score": round(float(cand.get("score", 1.0)), 5),
            }
        )

    if frame_stride > 1:
        frames = frames[::frame_stride]

    return {
        "fixture_id": fixture_id,
        "role": role,
        "session_group": session_group(fixture_id),
        "status": "included" if frames else "excluded",
        "reason": reason if frames else "no_accepted_frames",
        "total_frames": len(guarded),
        "accepted_frames": len(frames),
        "frames": frames,
    }


def build_coco_split(curated_clips: list[dict[str, Any]], fold: str) -> dict[str, Any]:
    """Build a deterministic COCO object-detection dictionary for a single fold ('train' or 'val')."""
    images: list[dict[str, Any]] = []
    annotations: list[dict[str, Any]] = []
    next_image_id = 1
    next_ann_id = 1

    for clip in sorted(curated_clips, key=lambda c: c["fixture_id"]):
        if clip.get("fold") != fold or clip.get("status") != "included":
            continue
        fixture_id = clip["fixture_id"]
        for frame in clip["frames"]:
            frame_idx = frame["frame_index"]
            file_name = f"frames/{fixture_id}/frame_{frame_idx:06d}.png"
            images.append(
                {
                    "id": next_image_id,
                    "file_name": file_name,
                    "width": frame["width_px"],
                    "height": frame["height_px"],
                    "fixture_id": fixture_id,
                    "frame_index": frame_idx,
                    "pts": frame["pts"],
                    "time_s": frame["time_s"],
                    "session_group": clip["session_group"],
                }
            )
            box_w, box_h = frame["bbox"][2], frame["bbox"][3]
            annotations.append(
                {
                    "id": next_ann_id,
                    "image_id": next_image_id,
                    "category_id": CATEGORY_ID,
                    "bbox": frame["bbox"],
                    "area": round(box_w * box_h, 4),
                    "iscrowd": 0,
                    "center_x_px": frame["center_x_px"],
                    "center_y_px": frame["center_y_px"],
                    "radius_px": frame["radius_px"],
                    "teacher_score": frame["teacher_score"],
                }
            )
            next_image_id += 1
            next_ann_id += 1

    return {
        "info": {
            "description": f"OpenBar Phase B teacher-distilled plate dataset ({fold} fold, machine-origin)",
            "version": "1.0",
            "harness": HARNESS_ID,
            "origin": "machine",
            "fold": fold,
        },
        "licenses": [
            {
                "id": 1,
                "name": "Research-only development split (see docs/legal/SAM3_LICENCE_REVIEW.md)",
            }
        ],
        "categories": [
            {
                "id": CATEGORY_ID,
                "name": CATEGORY_NAME,
                "supercategory": "equipment",
            }
        ],
        "images": images,
        "annotations": annotations,
    }


def extract_curated_frames(
    *,
    repo_root: Path,
    fixtures: dict[str, dict[str, Any]],
    curated_clips: list[dict[str, Any]],
    output_dir: Path,
) -> int:
    """Extract only the accepted frame indices for included clips into <output_dir>/frames/<fixture_id>/."""
    extracted_total = 0
    for clip in sorted(curated_clips, key=lambda c: c["fixture_id"]):
        if clip.get("status") != "included" or not clip.get("frames"):
            continue
        fixture_id = clip["fixture_id"]
        fixture = fixtures[fixture_id]
        if clips.development_role(fixture) != clip["role"]:
            raise clips.ClipRefused(f"{fixture_id}: development role changed before frame extraction")
        media = repo_root / fixture["media"]["repository_path"]
        label_package.require_media_hash(fixture, media)
        probed = label_package.probe(media)
        label_package.require_fixture_probe_match(fixture, probed)
        indices = [int(f["frame_index"]) for f in clip["frames"]]
        expected_pts = [int(f["pts"]) for f in clip["frames"]]
        if [probed["pts"][idx] for idx in indices] != expected_pts:
            raise label_package.PackageError(
                f"{fixture_id}: teacher frame PTS do not match probed media PTS"
            )
        frames_dir = output_dir / "frames" / fixture_id
        written = label_package.extract(media, indices, expected_pts, frames_dir, fixture)
        extracted_total += len(written)
    return extracted_total


def export_distillation_dataset(
    *,
    repo_root: Path,
    teacher_run_dir: Path,
    method: str,
    output_dir: Path,
    manifests: list[Path] | None = None,
    frame_stride: int = 1,
    extract_frames: bool = False,
) -> dict[str, Any]:
    """Curate and export train/val COCO annotations and dataset_manifest.json."""
    if frame_stride < 1:
        raise ValueError("frame_stride must be at least 1")
    repo_root = repo_root.resolve()
    teacher_run_dir = teacher_run_dir.resolve()
    output_dir = output_dir.resolve()
    if not output_dir.is_relative_to(label_package.PRIVATE_ROOT.resolve()):
        raise SystemExit(f"output must be under {label_package.PRIVATE_ROOT}")
    if output_dir.exists():
        raise SystemExit(f"{output_dir} already exists; choose a new --output-dir")

    manifest_paths = manifests or [
        repo_root / "validation" / "private" / "manifest.json",
        repo_root / "validation" / "private" / "vbt" / "manifest.json",
    ]
    fixtures = clips.load_fixtures(manifest_paths)
    run_json_path = teacher_run_dir / "run.json"
    if run_json_path.is_file():
        recorded_roles = json.loads(run_json_path.read_text(encoding="utf-8")).get("clips") or {}
        for name, role in recorded_roles.items():
            if name not in fixtures or clips.development_role(fixtures[name]) != role:
                raise clips.ClipRefused(f"{name}: missing fixture or development-role mismatch")

    allowed = clips.development_clips(fixtures)
    folds = assign_session_folds(list(allowed))

    candidates_dir = teacher_run_dir / "candidates" / method
    if not candidates_dir.is_dir():
        raise SystemExit(f"teacher candidates directory not found: {candidates_dir}")

    private_root = repo_root / "validation" / "private"
    curated_clips: list[dict[str, Any]] = []
    jsonl_hashes: dict[str, str] = {}

    for fixture_id in sorted(allowed):
        jsonl_path = candidates_dir / f"{fixture_id}.jsonl"
        if not jsonl_path.is_file():
            continue
        jsonl_hashes[fixture_id] = sha256_file(jsonl_path)
        records = [
            json.loads(line)
            for line in jsonl_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        truth_points = truth.load_truth(fixture_id, private_root)
        curated = curate_clip_records(
            fixtures[fixture_id], records, truth_points, frame_stride=frame_stride
        )
        curated["fold"] = folds[fixture_id]
        curated_clips.append(curated)

    train_coco = build_coco_split(curated_clips, "train")
    val_coco = build_coco_split(curated_clips, "val")

    ann_dir = output_dir / "annotations"
    ann_dir.mkdir(parents=True, exist_ok=True)
    train_path = ann_dir / "train.coco.json"
    val_path = ann_dir / "val.coco.json"
    train_path.write_text(json.dumps(train_coco, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    val_path.write_text(json.dumps(val_coco, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    extracted_frames = 0
    if extract_frames:
        extracted_frames = extract_curated_frames(
            repo_root=repo_root,
            fixtures=fixtures,
            curated_clips=curated_clips,
            output_dir=output_dir,
        )

    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=HERE, capture_output=True, text=True, check=False
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--", str(HERE)], cwd=HERE, capture_output=True, text=True, check=False
    ).stdout.strip()
    manifest_doc = {
        "harness": HARNESS_ID,
        "origin": "machine",
        "code_commit": commit,
        "dirty": bool(dirty),
        "source_sha256": {p.name: sha256_file(p) for p in sorted(HERE.glob("*.py"))},
        "frame_stride": frame_stride,
        "frames_extracted": extract_frames,
        "extracted_frame_count": extracted_frames,
        "teacher_run": {
            "path": teacher_run_dir.name,
            "method": method,
            "run_json_sha256": sha256_file(run_json_path) if run_json_path.is_file() else None,
            "clip_jsonl_sha256": jsonl_hashes,
        },
        "summary": {
            "total_clips": len(curated_clips),
            "included_clips": sum(1 for c in curated_clips if c["status"] == "included"),
            "excluded_clips": sum(1 for c in curated_clips if c["status"] == "excluded"),
            "train_images": len(train_coco["images"]),
            "val_images": len(val_coco["images"]),
        },
        "clips": [
            {k: v for k, v in clip.items() if k != "frames"}
            for clip in curated_clips
        ],
    }
    (output_dir / "dataset_manifest.json").write_text(
        json.dumps(manifest_doc, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest_doc


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo-root", type=Path, required=True, help="checkout holding validation/private")
    p.add_argument("--teacher-run-dir", type=Path, required=True, help="Phase A.2 / SAM 3 seed run directory")
    p.add_argument(
        "--method",
        default="track_sam2_sam3seed_guard",
        help="candidate directory name under <teacher-run-dir>/candidates/",
    )
    p.add_argument("--output-dir", type=Path, required=True, help="target directory under validation/private")
    p.add_argument("--manifest", type=Path, action="append")
    p.add_argument("--frame-stride", type=int, default=1, help="keep every Nth accepted frame per clip")
    p.add_argument("--extract-frames", action="store_true", help="decode accepted PNG frames into <output-dir>/frames/")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    manifest_doc = export_distillation_dataset(
        repo_root=args.repo_root,
        teacher_run_dir=args.teacher_run_dir,
        method=args.method,
        output_dir=args.output_dir,
        manifests=args.manifest,
        frame_stride=args.frame_stride,
        extract_frames=args.extract_frames,
    )
    print(json.dumps(manifest_doc["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
