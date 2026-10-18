#!/usr/bin/env python3
"""Phase B student detector training and validation harness (#233 Phase B).

Trains an Apache-2.0 single-class plate detector (``D-FINE-S``, ``ustc-community/dfine-small-coco``,
zero SAM 3 weights) on a curated Phase B COCO dataset produced by ``distill_dataset.py``, and scores
validation predictions on the session-disjoint ``val`` fold under ``identity-loose-v1``.

Pure validation, format conversion, and evaluation helpers are stdlib/numpy only and unit-tested
without loading model weights. Training imports ``torch`` and ``transformers`` lazily.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
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
import detectors  # noqa: E402
import geometry  # noqa: E402
import label_package  # noqa: E402
import selection  # noqa: E402

HARNESS_ID = "plate-detection-phase-b-train-v1"
STUDENT_CLASS_ID = 0
STUDENT_CLASS_NAME = "barbell_plate"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _percentile90(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, math.ceil(0.90 * len(ordered)) - 1))
    return round(ordered[idx], 4)


def validate_dataset_dir(
    dataset_dir: Path,
    repo_root: Path,
    manifests: list[Path] | None = None,
) -> dict[str, Any]:
    """Fail-closed admission check on a curated Phase B dataset directory before training or eval."""
    dataset_dir = dataset_dir.resolve()
    repo_root = repo_root.resolve()
    if not dataset_dir.is_relative_to(label_package.PRIVATE_ROOT.resolve()):
        raise SystemExit(f"dataset_dir must be under {label_package.PRIVATE_ROOT}")

    manifest_file = dataset_dir / "dataset_manifest.json"
    train_file = dataset_dir / "annotations" / "train.coco.json"
    val_file = dataset_dir / "annotations" / "val.coco.json"
    for req in (manifest_file, train_file, val_file):
        if not req.is_file():
            raise FileNotFoundError(f"missing required Phase B dataset file: {req}")

    dataset_manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    train_coco = json.loads(train_file.read_text(encoding="utf-8"))
    val_coco = json.loads(val_file.read_text(encoding="utf-8"))

    manifest_paths = manifests or [
        repo_root / "validation" / "private" / "manifest.json",
        repo_root / "validation" / "private" / "vbt" / "manifest.json",
    ]
    fixtures = clips.load_fixtures(manifest_paths)

    for clip in dataset_manifest.get("clips") or []:
        cid = clip.get("fixture_id", "<missing>")
        role = clip.get("role")
        if cid not in fixtures or clips.development_role(fixtures[cid]) != role:
            raise clips.ClipRefused(f"{cid}: missing fixture or development-role mismatch")

    for fold_name, coco in (("train", train_coco), ("val", val_coco)):
        for img in coco.get("images") or []:
            cid = img.get("fixture_id", "<missing>")
            if cid not in fixtures:
                raise clips.ClipRefused(f"{cid} in {fold_name}.coco.json is missing from manifests")
            clips.development_role(fixtures[cid])

    train_sessions = {img["session_group"] for img in train_coco.get("images") or []}
    val_sessions = {img["session_group"] for img in val_coco.get("images") or []}
    overlap = train_sessions & val_sessions
    if overlap:
        raise RuntimeError(f"session group leakage between train and val COCO splits: {sorted(overlap)}")

    return {
        "dataset_manifest": dataset_manifest,
        "train_coco": train_coco,
        "val_coco": val_coco,
        "hashes": {
            "dataset_manifest_sha256": sha256_file(manifest_file),
            "train_coco_sha256": sha256_file(train_file),
            "val_coco_sha256": sha256_file(val_file),
        },
    }


def coco_to_hf_annotations(
    image_entry: dict[str, Any],
    anns: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Convert a COCO image entry and its annotations to Hugging Face AutoImageProcessor format."""
    image_id = int(image_entry["id"])
    hf_anns: list[dict[str, Any]] = []
    for ann in anns:
        bbox = [float(v) for v in ann["bbox"]]
        if len(bbox) != 4 or not all(math.isfinite(v) for v in bbox):
            raise ValueError(f"non-finite bbox on image_id={image_id}: {bbox!r}")
        if bbox[2] <= 0.0 or bbox[3] <= 0.0:
            raise ValueError(f"non-positive bbox dimensions on image_id={image_id}: {bbox!r}")
        hf_anns.append(
            {
                "bbox": bbox,
                "category_id": STUDENT_CLASS_ID,
                "area": float(ann.get("area", bbox[2] * bbox[3])),
                "iscrowd": int(ann.get("iscrowd", 0)),
            }
        )
    return {"image_id": image_id, "annotations": hf_anns}


def evaluate_box_predictions(
    predictions_by_image_id: dict[int, Sequence[dict[str, Any]]],
    val_coco: dict[str, Any],
) -> dict[str, Any]:
    """Score per-image candidate rows against a Phase B COCO validation split."""
    images_by_id = {int(img["id"]): img for img in val_coco.get("images") or []}
    anns_by_image_id: dict[int, list[dict[str, Any]]] = {img_id: [] for img_id in images_by_id}
    for ann in val_coco.get("annotations") or []:
        img_id = int(ann["image_id"])
        if img_id in anns_by_image_id:
            anns_by_image_id[img_id].append(ann)

    total_frames = 0
    recall_hits = 0
    top1_hits = 0
    plausible_top1_hits = 0
    refused_count = 0
    accepted_count = 0
    accepted_correct = 0
    ious: list[float] = []
    centre_errs_rel: list[float] = []
    radius_errs_rel: list[float] = []

    for img_id in sorted(images_by_id):
        gt_anns = anns_by_image_id.get(img_id) or []
        if not gt_anns:
            continue
        gt = gt_anns[0]
        gt_cx = float(gt["center_x_px"])
        gt_cy = float(gt["center_y_px"])
        gt_r = float(gt["radius_px"])
        gt_xyxy = [
            float(gt["bbox"][0]),
            float(gt["bbox"][1]),
            float(gt["bbox"][0] + gt["bbox"][2]),
            float(gt["bbox"][1] + gt["bbox"][3]),
        ]
        img_meta = images_by_id[img_id]
        width_px = int(img_meta["width"])
        height_px = int(img_meta["height"])

        rows = list(predictions_by_image_id.get(img_id) or [])
        cands = [selection.Candidate.from_dict(r) for r in rows]
        window = selection.in_frame(selection.plausible(cands, width_px, height_px), width_px, height_px)
        refusal = selection.margin_refusal(window)

        total_frames += 1
        top_raw = selection.select_top1(cands)
        top_plausible = selection.select_top1(window)

        if top_raw is not None and selection.match(top_raw, gt_cx, gt_cy, gt_r):
            top1_hits += 1
        plausible_ok = top_plausible is not None and selection.match(top_plausible, gt_cx, gt_cy, gt_r)
        if plausible_ok:
            plausible_top1_hits += 1

        if refusal["refused"]:
            refused_count += 1
        else:
            accepted_count += 1
            if plausible_ok:
                accepted_correct += 1

        matched_rows = [
            (r, selection.Candidate.from_dict(r))
            for r in rows
            if selection.match(selection.Candidate.from_dict(r), gt_cx, gt_cy, gt_r)
        ]
        if matched_rows:
            recall_hits += 1
            best_row, best_cand = min(
                matched_rows,
                key=lambda item: math.hypot(item[1].center_x_px - gt_cx, item[1].center_y_px - gt_cy),
            )
            centre_errs_rel.append(
                math.hypot(best_cand.center_x_px - gt_cx, best_cand.center_y_px - gt_cy) / gt_r
            )
            radius_errs_rel.append(abs(best_cand.radius_px - gt_r) / gt_r)
            if "box_xyxy" in best_row:
                ious.append(detectors.box_iou([float(v) for v in best_row["box_xyxy"]], gt_xyxy))
            else:
                pred_xyxy = [
                    best_cand.center_x_px - best_cand.radius_px,
                    best_cand.center_y_px - best_cand.radius_px,
                    best_cand.center_x_px + best_cand.radius_px,
                    best_cand.center_y_px + best_cand.radius_px,
                ]
                ious.append(detectors.box_iou(pred_xyxy, gt_xyxy))

    return {
        "evaluated_frames": total_frames,
        "frame_recall": recall_hits,
        "frame_recall_rate": round(recall_hits / total_frames, 4) if total_frames else None,
        "frame_top1": top1_hits,
        "frame_top1_rate": round(top1_hits / total_frames, 4) if total_frames else None,
        "frame_plausible_top1": plausible_top1_hits,
        "frame_plausible_top1_rate": round(plausible_top1_hits / total_frames, 4) if total_frames else None,
        "refusal": {
            "rule": selection.MARGIN_RULE,
            "refused": refused_count,
            "accepted": accepted_count,
            "accepted_correct": accepted_correct,
        },
        "box_iou_median": round(statistics.median(ious), 4) if ious else None,
        "box_iou_p90": _percentile90(ious),
        "centre_error_rel_median": round(statistics.median(centre_errs_rel), 4) if centre_errs_rel else None,
        "centre_error_rel_p90": _percentile90(centre_errs_rel),
        "radius_abs_error_rel_median": round(statistics.median(radius_errs_rel), 4) if radius_errs_rel else None,
        "radius_abs_error_rel_p90": _percentile90(radius_errs_rel),
    }


def train_dfine_student(
    *,
    dataset_dir: Path,
    repo_root: Path,
    output_dir: Path,
    base_checkpoint: str = detectors.DFINE_CHECKPOINT,
    epochs: int = 10,
    batch_size: int = 4,
    learning_rate: float = 1e-4,
    seed: int = 0,
    manifests: list[Path] | None = None,
) -> dict[str, Any]:
    """Fine-tune D-FINE-S on the curated train fold and evaluate on the session-disjoint val fold."""
    output_dir = output_dir.resolve()
    if not output_dir.is_relative_to(label_package.PRIVATE_ROOT.resolve()):
        raise SystemExit(f"output_dir must be under {label_package.PRIVATE_ROOT}")
    if output_dir.exists():
        raise SystemExit(f"{output_dir} already exists; choose a new --output-dir")

    validated = validate_dataset_dir(dataset_dir, repo_root, manifests=manifests)
    train_coco = validated["train_coco"]
    val_coco = validated["val_coco"]

    import cv2
    import torch
    from PIL import Image
    from torch.utils.data import DataLoader, Dataset
    from transformers import AutoImageProcessor, DFineForObjectDetection

    torch.manual_seed(seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    processor = AutoImageProcessor.from_pretrained(base_checkpoint)
    model = DFineForObjectDetection.from_pretrained(
        base_checkpoint,
        num_labels=1,
        id2label={STUDENT_CLASS_ID: STUDENT_CLASS_NAME},
        label2id={STUDENT_CLASS_NAME: STUDENT_CLASS_ID},
        ignore_mismatched_sizes=True,
    ).to(device)

    class _CocoDataset(Dataset):
        def __init__(self, coco_dict: dict[str, Any], root: Path) -> None:
            self.images = list(coco_dict.get("images") or [])
            self.anns_by_id: dict[int, list[dict[str, Any]]] = {int(img["id"]): [] for img in self.images}
            for ann in coco_dict.get("annotations") or []:
                img_id = int(ann["image_id"])
                if img_id in self.anns_by_id:
                    self.anns_by_id[img_id].append(ann)
            self.root = root

        def __len__(self) -> int:
            return len(self.images)

        def __getitem__(self, idx: int) -> tuple[Image.Image, dict[str, Any]]:
            img_entry = self.images[idx]
            img_path = self.root / img_entry["file_name"]
            if not img_path.is_file():
                raise FileNotFoundError(
                    f"missing extracted frame {img_path}; run distill_dataset.py --extract-frames first"
                )
            pil = Image.open(img_path).convert("RGB")
            target = coco_to_hf_annotations(img_entry, self.anns_by_id[int(img_entry["id"])])
            return pil, target

    def _collate(batch: list[tuple[Image.Image, dict[str, Any]]]) -> dict[str, Any]:
        images = [item[0] for item in batch]
        annotations = [item[1] for item in batch]
        return processor(images=images, annotations=annotations, return_tensors="pt")

    train_ds = _CocoDataset(train_coco, dataset_dir.resolve())
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True, collate_fn=_collate)
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate, weight_decay=1e-4)

    output_dir.mkdir(parents=True, exist_ok=True)
    best_checkpoint_dir = output_dir / "best_checkpoint"
    best_recall = -1.0
    history: list[dict[str, Any]] = []

    for epoch in range(1, epochs + 1):
        started = time.perf_counter()
        model.train()
        total_loss = 0.0
        steps = 0
        for batch in train_loader:
            pixel_values = batch["pixel_values"].to(device)
            labels = [{k: v.to(device) for k, v in t.items()} for t in batch["labels"]]
            optimizer.zero_grad()
            outputs = model(pixel_values=pixel_values, labels=labels)
            loss = outputs.loss
            loss.backward()
            optimizer.step()
            total_loss += float(loss.detach().cpu().item())
            steps += 1

        model.eval()
        preds_by_id: dict[int, list[dict[str, Any]]] = {}
        for img_entry in val_coco.get("images") or []:
            img_path = dataset_dir.resolve() / img_entry["file_name"]
            bgr = cv2.imread(str(img_path), cv2.IMREAD_COLOR)
            if bgr is None:
                raise FileNotFoundError(f"cannot read validation frame {img_path}")
            pil = Image.fromarray(bgr[:, :, ::-1].copy())
            inputs = processor(images=pil, return_tensors="pt").to(device)
            with torch.no_grad():
                out = model(**inputs)
            res = processor.post_process_object_detection(
                out, threshold=detectors.DFINE_SCORE_THRESHOLD, target_sizes=[(pil.size[1], pil.size[0])]
            )[0]
            boxes = res["boxes"].float().tolist()
            scores = res["scores"].float().tolist()
            rows = []
            for i in detectors.dedupe(boxes, scores):
                circle = geometry.circle_from_box(*boxes[i])
                rows.append(
                    detectors._row(circle, scores[i], box_xyxy=[round(v, 2) for v in boxes[i]])
                )
            preds_by_id[int(img_entry["id"])] = rows

        val_metrics = evaluate_box_predictions(preds_by_id, val_coco)
        epoch_record = {
            "epoch": epoch,
            "train_loss_mean": round(total_loss / max(1, steps), 5),
            "elapsed_s": round(time.perf_counter() - started, 2),
            "val": val_metrics,
        }
        history.append(epoch_record)
        cur_score = float(val_metrics.get("frame_plausible_top1_rate") or 0.0)
        if cur_score > best_recall:
            best_recall = cur_score
            best_checkpoint_dir.mkdir(parents=True, exist_ok=True)
            model.save_pretrained(best_checkpoint_dir)
            processor.save_pretrained(best_checkpoint_dir)

    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=HERE, capture_output=True, text=True, check=False
    ).stdout.strip()
    checkpoint_hashes = {
        p.name: sha256_file(p)
        for p in sorted(best_checkpoint_dir.glob("*"))
        if p.is_file()
    }
    record = {
        "harness": HARNESS_ID,
        "origin": "machine",
        "code_commit": commit,
        "python": platform.python_version(),
        "student_architecture": {
            "base_checkpoint": base_checkpoint,
            "model_class": "DFineForObjectDetection",
            "license": "Apache-2.0",
            "contains_sam3_weights": False,
        },
        "training_config": {
            "epochs": epochs,
            "batch_size": batch_size,
            "learning_rate": learning_rate,
            "seed": seed,
        },
        "dataset": validated["hashes"],
        "best_checkpoint_files_sha256": checkpoint_hashes,
        "epochs": history,
    }
    (output_dir / "student_run.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return record


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dataset-dir", type=Path, required=True, help="curated Phase B dataset directory")
    p.add_argument("--repo-root", type=Path, required=True, help="checkout holding validation/private manifests")
    p.add_argument("--output-dir", type=Path, required=True, help="new output directory under validation/private")
    p.add_argument("--base-checkpoint", default=detectors.DFINE_CHECKPOINT)
    p.add_argument("--epochs", type=int, default=10)
    p.add_argument("--batch-size", type=int, default=4)
    p.add_argument("--learning-rate", type=float, default=1e-4)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--manifest", type=Path, action="append")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    record = train_dfine_student(
        dataset_dir=args.dataset_dir,
        repo_root=args.repo_root,
        output_dir=args.output_dir,
        base_checkpoint=args.base_checkpoint,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        seed=args.seed,
        manifests=args.manifest,
    )
    print(json.dumps(record["epochs"][-1] if record["epochs"] else {}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
