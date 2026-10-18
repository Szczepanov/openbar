# Plate detection Phase B: teacher-to-student distillation plan (#233)

Date: 2026-10-18. Work type: **research plan and dataset curation harness** (#233 Phase B, approved by the
owner on 2026-10-17). Parent: [PLATE_DETECTION_SAM3_SEED_RESULTS.md](../analysis/PLATE_DETECTION_SAM3_SEED_RESULTS.md),
[SAM3_LICENCE_REVIEW.md](../legal/SAM3_LICENCE_REVIEW.md). Nothing here authorizes production automatic
selection (#178's successor) or M1 entry (#80).

## 1. Context and current state

In Phase A and Phase A.2 (#233), `sam3-seed-v2` (SAM 3 text prompt `"weight plate"` / `"barbell plate"` on
frame 0, filtered by the plate-size window, `in-frame-v1`, and the `sam3-margin-v2` refusal rule) selected the
labelled plate on **24/25** scored development clips (**19/20** on a label-free frame-0 decision). Paired with
SAM 2.1 video tracking (`track_sam2_sam3seed`), it stayed on the labelled plate across **316/388** labelled
frames (24/25 clips on target on every labelled frame, 0 lost frames out of 11,719 tracked frames).

However:

1. **SAM 3 cannot run on a phone.** It is a ~850M-parameter foundation model under Meta's SAM License, taking
   ~0.72 s/frame on a desktop RTX 3060 Ti.
2. **Phone trackers need both a frame-0 seed and post-loss re-detection.** On the same seeds, EdgeTAM
   (`yonigozlan/EdgeTAM-hf`, Apache-2.0, 13.9M parameters) matched SAM 2.1 on 24/25 clips at a quarter of the
   desktop runtime, and `track-guard-v2` (`research/plate-detection/guard.py`) prevented its silent wrong
   re-lock on `self-clean-side-0203-17` by stopping the track after 0.5 s of rejected/lost frames. Resuming a
   stopped track requires a lightweight detector that can re-propose the plate on the phone.
3. **Licence boundary (`docs/legal/SAM3_LICENCE_REVIEW.md`).** Meta's SAM License does not restrict masks,
   boxes, or scores produced by SAM 3 and is silent on training other models from those outputs. The owner
   approved Phase B on 2026-10-17: train a small **Apache-2.0 or MIT** detector from SAM 3 + SAM 2.1 teacher
   outputs on development clips only, containing zero SAM 3 weights.

## 2. Goal and explicit non-goals

### Goal

1. Build a deterministic, leak-free teacher dataset curation harness (`research/plate-detection/distill_dataset.py`)
   that converts guarded teacher tracks (`track_sam2_sam3seed` after `track-guard-v2`, plus optional owner
   human annotations where available on development clips) into a session-grouped COCO-format bounding-box
   dataset (`[x_min, y_min, width, height]` derived from `(center_x_px, center_y_px, radius_px)`).
2. Train and compare two Apache-2.0 real-time detector candidates suitable for mobile NPU/CPU execution on the
   reference device (**Xiaomi 14T Pro**, Dimensity 9300+, Android 16, ADR-0009 / #179):
   - **D-FINE-S** (`ustc-community/dfine-small-coco`, Apache-2.0, ~10M params, NMS-free DETR with fine-grained
     distribution refinement);
   - **RF-DETR-Nano** (`roboflow/rf-detr`, Apache-2.0, lightweight DETR variant).
3. Evaluate both candidates on (a) label-free frame-0 seed selection with refusal margin and (b) post-loss
   re-detection when `track-guard-v2` stops a track.
4. Export the better candidate to ONNX / LiteRT and measure on-device inference latency on the Xiaomi 14T Pro
   against the #179 budget (`< 5.0 s` median / `< 8.0 s` max total post-set latency).

### Non-goals

- Bundling or redistributing SAM 3 weights, or initializing student weights from SAM 3.
- Using any clip tagged `split-reserved-57`, `purpose: validation`, `split-frozen-eval`, pilot (`7` boundary
  clips), or refusal-candidate (`15` OOD/no-lift clips).
- Replacing human rim confirmation (#95) or sub-pixel rim fitting for metric calibration scale (`±0.5%` radius
  requirement). A bounding-box detector locates the plate (`center_x, center_y, radius_px ≈ min(w, h) / 2`) to
  seed or re-acquire tracking; scale still requires rim refinement or user confirmation.
- Modifying `openbar-core`, canonical `Analysis`, or JSON schemas.

## 3. Governing architecture, licensing, and split constraints

| Rule | Enforcement |
| --- | --- |
| **Held-out & split refusal** | `validation/tools/held_out_guard.py` (`is_held_out`) and `research/plate-detection/clips.py` (`development_role`) refuse everything except the 27 development clips (`14 split-exploration` + `13 scored-development-only`). |
| **Session-grouped train/val split** | Clips from the same recording session (`2026-10-15`, `2026-10-03` `vbt-*`, `2023-02-*`, etc.) are assigned to the same fold (`session_group(fixture_id)`), preventing near-duplicate background leakage between student training and development validation. |
| **Teacher label hygiene** | Clips whose frame-0 seed was refused by `sam3-margin-v2` or known to be off-target on development truth (`self-other-side-0215-27`) are excluded from positive box supervision unless overridden by human truth. Frames marked lost or rejected by `track-guard-v2` emit no positive box. |
| **Provenance** | Every exported dataset writes `dataset_manifest.json` recording the teacher `run.json` SHA-256, per-clip JSONL SHA-256, clip roles, session-group assignments, and filter rules. |
| **Clean-room & licensing (ADR-0011)** | Only Apache-2.0 / MIT student architectures (`D-FINE-S`, `RF-DETR-Nano`). Ultralytics YOLO (AGPL-3.0) remains excluded. |

## 4. Ordered work packages

### WP1 — Teacher dataset curation & session-grouped COCO export (implemented)

- **Module:** `research/plate-detection/distill_dataset.py` (stdlib-only, model-free).
- **Inputs:**
  - Development fixtures from `validation/private/manifest.json` and `validation/private/vbt/manifest.json`,
    filtered strictly through `clips.development_role` and `held_out_guard.is_held_out`.
  - Guarded teacher track directory (e.g. `validation/private/plate-detection-sam3-seed/run-2-guard-v2/candidates/track_sam2_sam3seed_guard_v2/`).
  - Optional development truth annotations (`truth.load_truth`) to veto any teacher track whose accepted seed
    disagrees with human labels (`center_err / r > 0.25` or `radius_err > 0.25`).
- **Outputs (under `validation/private/plate-detection-phase-b/`):**
  - `dataset_manifest.json`: provenance, teacher hashes, session fold assignment, accepted/skipped frame counts.
  - `annotations/train.coco.json` and `annotations/val.coco.json`: deterministic COCO-format single-class
    (`category_id: 1, name: "barbell_plate"`) bounding-box annotations with circle metadata (`center_x_px`,
    `center_y_px`, `radius_px`, `teacher_score`, `fixture_id`, `frame_index`, `pts`, `time_s`).
- **Tests:** `research/plate-detection/tests/test_distill_dataset.py` covering held-out/pilot refusal,
  session-group fold isolation, refused/off-target seed rejection, lost-frame exclusion, and COCO box geometry.

### WP2 — Student training harness (`D-FINE-S` and `RF-DETR-Nano`)

- Extract only the referenced decoded frames (`label_package.extract`) into the private dataset directory.
- Fine-tune `D-FINE-S` and `RF-DETR-Nano` on the `train` fold (640×640 letterboxed display-oriented frames,
  horizontal flip augmentation, cosine schedule, fixed seed `0`), holding out the session-disjoint `val` fold.
- Record checkpoint SHA-256, package versions, and training config in `run.json`.

### WP3 — Development evaluation & `track-guard-v2` re-acquisition

- Score student proposals on the held-out development `val` fold using `score_phase_a.py` (`identity-loose-v1`
  and `pilot-tctr`).
- Integrate optional student re-detection into `guard.py`: when `track-guard-v2` stops a track after `0.5 s`
  of rejected/lost frames, query the student detector and resume tracking only if the candidate passes the
  seed radius consistency check (`|r - r_seed| / r_seed <= 0.15`).

### WP4 — ONNX / LiteRT export & Xiaomi 14T Pro latency benchmark (#179)

- Export the student detector to static-shape ONNX / LiteRT (`fp16` and `int8` calibrated on the `train` fold).
- Benchmark frame-0 seed detection latency on the **Xiaomi 14T Pro** (`2407FPN8EG`, Dimensity 9300+) via
  LiteRT `benchmark_model` or ONNX Runtime Mobile, recording results in `docs/validation/ANDROID_LATENCY_EVIDENCE.md`.

### WP5 — Frozen evaluation on new independent sessions

- Blocked on the owner recording at least 2 new side-view sessions tagged `split-frozen-eval`
  ([PLATE_SELECTION_NEXT_ROUND_PLAN.md](PLATE_SELECTION_NEXT_ROUND_PLAN.md) WP1–WP5) and recording a decision
  on #178's successor.

## 5. Verification commands

```bash
python -m unittest discover -v -s research/plate-detection/tests -p 'test_*.py'
python -m unittest discover -v -s validation/tests -p 'test_*.py'
python validation/tools/schema_check.py
```

## 6. Stop conditions

- Stop if every Apache-2.0 student candidate falls below 85% top-1 recall on the session-disjoint development
  validation fold or exceeds `250 ms` per-frame latency on the Xiaomi 14T Pro (leaving manual tap seeding in
  `research/phone-ui/` as the primary phone seed path).
- Never promote a student model into canonical `Analysis` or production `vbt_suggest.py` without a frozen
  evaluation on new `split-frozen-eval` sessions and an explicit owner decision.
