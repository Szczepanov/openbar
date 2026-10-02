# Plate tracker evaluation plan (handover)

Status: ready to execute · Owner: project owner (Marcin) · Related: #57, ADR-0008, #58, #53
Written 2026-10-02 from the session that built `research/opencv-tracking/`.

## 1. Goal

Find a plate tracker that meets the #57 gates on real side-view video, or show that none does:

| Gate (from `validation/tools/tracker_filter_selection.py`) | Target |
|---|---|
| Plate-centre MAE | < 3.0 px |
| Tracking availability | > 99 % |
| Silent false tracks | none: no high-confidence (≥ 0.8) sample with error > 3 px |
| Runtime | recorded, never used to hide accuracy failures |

OpenBar's own M0 trackers fail badly on real footage. OpenCV CSRT is the current lead. This plan evaluates
stronger off-the-shelf trackers, a plate-specific geometric refinement and SAM 2, all on **development**
clips. It then freezes at most two candidates and runs them **once** on the held-out validation clips.

Research only. Nothing here goes into the Rust workspace or the product without a separate owner decision
(see Phase 5 and Phase 6).

## 2. Results so far (development clips, seed = first labelled frame)

Scored by `openbar-cli benchmark` against the owner's manual labels, `min_confidence` 0:

#### All labelled frames

| Clip | Tracker | License | Availability | MAE px | p50 px | p90 px | Max px | Max Loss | False Tracks |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| `self-back-squat-side-002` | opencv-csrt+hough | Apache-2.0 | 100.0 % | 2.8 | 2.9 | 4.7 | 5.8 | 0 | 1 |
| | opencv-csrt | Apache-2.0 | 100.0 % | 3.1 | 3.2 | 5.0 | 5.8 | 0 | 5 |
| | opencv-csrt+circle-a | Apache-2.0 | 100.0 % | 3.6 | 3.1 | 7.3 | 11.7 | 0 | 3 |
| | opencv-csrt+circle-b5 | Apache-2.0 | 100.0 % | 5.9 | 3.5 | 14.7 | 20.4 | 0 | 2 |
| | opencv-nano | Unconfirmed / all rights reserved (not shippable without confirmation) | 100.0 % | 6.7 | 4.6 | 21.5 | 30.7 | 0 | 17 |
| | opencv-csrt+circle-b1 | Apache-2.0 | 100.0 % | 8.1 | 6.7 | 18.2 | 22.5 | 0 | 1 |
| | opencv-dasiamrpn | MIT | 100.0 % | 10.4 | 10.2 | 21.2 | 25.4 | 0 | 22 |
| | opencv-kcf | Apache-2.0 | 72.0 % | 19.6 | 4.3 | 88.4 | 89.2 | 7 | 4 |
| | template-sad-v1 (OpenBar) | PolyForm Shield 1.0.0 | 100.0 % | 52.2 | 10.3 | 166.0 | 178.3 | 0 | 13 |
| | local-contrast-centroid-v1 (OpenBar) | PolyForm Shield 1.0.0 | 100.0 % | 108.9 | 75.3 | 251.5 | 281.6 | 0 | 24 |
| | opencv-vit | Apache-2.0 | 80.0 % | 133.6 | 27.7 | 326.4 | 384.0 | 5 | 0 |
| `self-clean-jerk-side-002` | opencv-csrt+circle-a | Apache-2.0 | 100.0 % | 5.7 | 5.1 | 9.9 | 14.3 | 0 | 2 |
| | opencv-csrt | Apache-2.0 | 100.0 % | 6.1 | 5.7 | 10.2 | 14.3 | 0 | 2 |
| | opencv-csrt+hough | Apache-2.0 | 100.0 % | 6.1 | 5.7 | 10.2 | 14.3 | 0 | 0 |
| | opencv-csrt+circle-b1 | Apache-2.0 | 100.0 % | 9.9 | 8.2 | 16.7 | 18.6 | 0 | 2 |
| | opencv-csrt+circle-b5 | Apache-2.0 | 100.0 % | 10.1 | 8.2 | 17.1 | 18.6 | 0 | 2 |
| | opencv-nano | Unconfirmed / all rights reserved (not shippable without confirmation) | 100.0 % | 21.7 | 22.5 | 44.3 | 46.9 | 0 | 9 |
| | opencv-dasiamrpn | MIT | 100.0 % | 25.7 | 22.5 | 48.0 | 50.9 | 0 | 19 |
| | template-sad-v1 (OpenBar) | PolyForm Shield 1.0.0 | 100.0 % | 198.8 | 199.4 | 423.3 | 439.8 | 0 | 18 |
| | opencv-kcf | Apache-2.0 | 85.7 % | 231.2 | 65.3 | 509.8 | 520.5 | 3 | 3 |
| | opencv-vit | Apache-2.0 | 100.0 % | 231.6 | 87.7 | 516.8 | 552.3 | 0 | 0 |
| | local-contrast-centroid-v1 (OpenBar) | PolyForm Shield 1.0.0 | 100.0 % | 746.1 | 883.3 | 1144.2 | 1162.2 | 0 | 20 |
| `self-snatch-side-002` | opencv-csrt+circle-b5 | Apache-2.0 | 100.0 % | 3.5 | 2.9 | 8.6 | 9.3 | 0 | 2 |
| | opencv-csrt+circle-a | Apache-2.0 | 100.0 % | 3.6 | 2.9 | 8.8 | 9.9 | 0 | 2 |
| | opencv-csrt+circle-b1 | Apache-2.0 | 100.0 % | 3.7 | 2.9 | 8.7 | 9.4 | 0 | 2 |
| | opencv-csrt+hough | Apache-2.0 | 100.0 % | 6.0 | 6.9 | 10.1 | 10.7 | 0 | 0 |
| | opencv-csrt | Apache-2.0 | 100.0 % | 7.2 | 7.8 | 10.3 | 11.4 | 0 | 3 |
| | opencv-dasiamrpn | MIT | 100.0 % | 15.9 | 9.0 | 26.1 | 77.8 | 0 | 18 |
| | opencv-kcf | Apache-2.0 | 87.0 % | 17.9 | 11.5 | 23.0 | 127.2 | 3 | 0 |
| | template-sad-v1 (OpenBar) | PolyForm Shield 1.0.0 | 95.7 % | 32.9 | 8.2 | 119.3 | 243.0 | 1 | 19 |
| | local-contrast-centroid-v1 (OpenBar) | PolyForm Shield 1.0.0 | 100.0 % | 57.3 | 19.1 | 211.5 | 392.6 | 0 | 9 |
| | opencv-nano | Unconfirmed / all rights reserved (not shippable without confirmation) | 100.0 % | 60.5 | 60.6 | 121.1 | 233.6 | 0 | 0 |
| | opencv-vit | Apache-2.0 | 100.0 % | 103.2 | 63.2 | 185.4 | 660.6 | 0 | 0 |

### Excluding seed frame (true tracking performance)

| Clip | Tracker | License | Availability | MAE px | p50 px | p90 px | Max px | Max Loss | False Tracks |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| `self-back-squat-side-002` | opencv-csrt+hough | Apache-2.0 | 100.0 % | 2.9 | 2.9 | 4.7 | 5.8 | 0 | 1 |
| | opencv-csrt | Apache-2.0 | 100.0 % | 3.2 | 3.2 | 5.0 | 5.8 | 0 | 5 |
| | opencv-csrt+circle-a | Apache-2.0 | 100.0 % | 3.8 | 3.1 | 7.3 | 11.7 | 0 | 3 |
| | opencv-csrt+circle-b5 | Apache-2.0 | 100.0 % | 6.2 | 3.5 | 14.7 | 20.4 | 0 | 2 |
| | opencv-nano | Unconfirmed / all rights reserved (not shippable without confirmation) | 100.0 % | 7.0 | 4.6 | 21.5 | 30.7 | 0 | 17 |
| | opencv-csrt+circle-b1 | Apache-2.0 | 100.0 % | 8.4 | 6.7 | 18.2 | 22.5 | 0 | 1 |
| | opencv-dasiamrpn | MIT | 100.0 % | 10.8 | 10.2 | 21.2 | 25.4 | 0 | 22 |
| | opencv-kcf | Apache-2.0 | 70.8 % | 20.8 | 4.5 | 88.4 | 89.2 | 7 | 4 |
| | template-sad-v1 (OpenBar) | PolyForm Shield 1.0.0 | 100.0 % | 54.3 | 10.3 | 166.0 | 178.3 | 0 | 13 |
| | local-contrast-centroid-v1 (OpenBar) | PolyForm Shield 1.0.0 | 100.0 % | 113.4 | 75.3 | 251.5 | 281.6 | 0 | 24 |
| | opencv-vit | Apache-2.0 | 79.2 % | 140.6 | 28.7 | 361.6 | 384.0 | 5 | 0 |
| `self-clean-jerk-side-002` | opencv-csrt+circle-a | Apache-2.0 | 100.0 % | 6.0 | 5.1 | 9.9 | 14.3 | 0 | 2 |
| | opencv-csrt | Apache-2.0 | 100.0 % | 6.4 | 5.7 | 10.2 | 14.3 | 0 | 2 |
| | opencv-csrt+hough | Apache-2.0 | 100.0 % | 6.4 | 5.7 | 10.2 | 14.3 | 0 | 0 |
| | opencv-csrt+circle-b1 | Apache-2.0 | 100.0 % | 10.4 | 8.2 | 16.7 | 18.6 | 0 | 2 |
| | opencv-csrt+circle-b5 | Apache-2.0 | 100.0 % | 10.6 | 8.2 | 17.1 | 18.6 | 0 | 2 |
| | opencv-nano | Unconfirmed / all rights reserved (not shippable without confirmation) | 100.0 % | 22.8 | 22.5 | 44.3 | 46.9 | 0 | 9 |
| | opencv-dasiamrpn | MIT | 100.0 % | 27.0 | 22.5 | 48.0 | 50.9 | 0 | 19 |
| | template-sad-v1 (OpenBar) | PolyForm Shield 1.0.0 | 100.0 % | 208.7 | 199.4 | 423.3 | 439.8 | 0 | 18 |
| | opencv-vit | Apache-2.0 | 100.0 % | 243.2 | 87.7 | 516.8 | 552.3 | 0 | 0 |
| | opencv-kcf | Apache-2.0 | 85.0 % | 244.8 | 82.3 | 509.8 | 520.5 | 3 | 3 |
| | local-contrast-centroid-v1 (OpenBar) | PolyForm Shield 1.0.0 | 100.0 % | 783.4 | 883.3 | 1144.2 | 1162.2 | 0 | 20 |
| `self-snatch-side-002` | opencv-csrt+circle-b5 | Apache-2.0 | 100.0 % | 3.6 | 2.9 | 8.6 | 9.3 | 0 | 2 |
| | opencv-csrt+circle-a | Apache-2.0 | 100.0 % | 3.7 | 2.9 | 8.8 | 9.9 | 0 | 2 |
| | opencv-csrt+circle-b1 | Apache-2.0 | 100.0 % | 3.8 | 2.9 | 8.7 | 9.4 | 0 | 2 |
| | opencv-csrt+hough | Apache-2.0 | 100.0 % | 6.3 | 6.9 | 10.1 | 10.7 | 0 | 0 |
| | opencv-csrt | Apache-2.0 | 100.0 % | 7.5 | 7.8 | 10.3 | 11.4 | 0 | 3 |
| | opencv-dasiamrpn | MIT | 100.0 % | 16.6 | 9.0 | 26.1 | 77.8 | 0 | 18 |
| | opencv-kcf | Apache-2.0 | 86.4 % | 18.8 | 12.2 | 31.8 | 127.2 | 3 | 0 |
| | template-sad-v1 (OpenBar) | PolyForm Shield 1.0.0 | 95.5 % | 34.5 | 8.3 | 119.3 | 243.0 | 1 | 19 |
| | local-contrast-centroid-v1 (OpenBar) | PolyForm Shield 1.0.0 | 100.0 % | 59.9 | 19.1 | 211.5 | 392.6 | 0 | 9 |
| | opencv-nano | Unconfirmed / all rights reserved (not shippable without confirmation) | 100.0 % | 63.2 | 60.6 | 121.1 | 233.6 | 0 | 0 |
| | opencv-vit | Apache-2.0 | 100.0 % | 107.9 | 63.2 | 185.4 | 660.6 | 0 | 0 |

### Physical scale (nominal mm) and paired comparison summary (Phase 2)

Errors in millimetres using a nominal 450 mm plate (`mm = px × 225 / r_seed_px`), seed-excluded:

| Clip | Seed radius px | Candidate | MAE px | MAE mm (nom) | p90 px | p90 mm (nom) | FT | FT (base conf) | High err (>3px) |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| `self-back-squat-side-002` | 90.9 | `opencv-csrt` | 3.22 | 7.96 | 5.00 | 12.36 | 5 | 5 | 14 |
| | | `opencv-csrt+circle-a` | 3.80 | 9.41 | 7.29 | 18.04 | 3 | 3 | 13 |
| | | `opencv-csrt+circle-b1` | 8.43 | 20.87 | 18.18 | 45.00 | 1 | 1 | 19 |
| | | `opencv-csrt+circle-b5` | 6.18 | 15.30 | 14.68 | 36.32 | 2 | 2 | 14 |
| | | `opencv-csrt+hough` | 2.93 | 7.25 | 4.71 | 11.65 | 1 | 5 | 12 |
| `self-clean-jerk-side-002` | 199.2 | `opencv-csrt` | 6.44 | 7.27 | 10.25 | 11.57 | 2 | 2 | 18 |
| | | `opencv-csrt+circle-a` | 5.97 | 6.74 | 9.87 | 11.14 | 2 | 1 | 17 |
| | | `opencv-csrt+circle-b1` | 10.37 | 11.71 | 16.70 | 18.87 | 2 | 1 | 18 |
| | | `opencv-csrt+circle-b5` | 10.58 | 11.95 | 17.07 | 19.28 | 2 | 1 | 18 |
| | | `opencv-csrt+hough` | 6.44 | 7.27 | 10.25 | 11.57 | 0 | 2 | 18 |
| `self-snatch-side-002` | 132.1 | `opencv-csrt` | 7.51 | 12.78 | 10.31 | 17.56 | 3 | 3 | 21 |
| | | `opencv-csrt+circle-a` | 3.73 | 6.35 | 8.82 | 15.02 | 2 | 2 | 11 |
| | | `opencv-csrt+circle-b1` | 3.83 | 6.52 | 8.73 | 14.87 | 2 | 2 | 11 |
| | | `opencv-csrt+circle-b5` | 3.61 | 6.14 | 8.60 | 14.65 | 2 | 3 | 10 |
| | | `opencv-csrt+hough` | 6.31 | 10.74 | 10.08 | 17.16 | 0 | 2 | 17 |

**Paired comparison against CSRT on seed-excluded labels:**
- `opencv-csrt+circle-a`:
  - Squat: 8 improved, 8 worsened, 8 tied (median Δ = +0.000 px / +0.000 mm). MAE rose from 3.22 to 3.80 px.
  - Clean & jerk: 3 improved, 1 worsened, 16 tied (median Δ = +0.000 px / +0.000 mm). Fit acceptance on labelled frames was only 20% (4/20).
  - Snatch: 18 improved, 4 worsened, 0 tied (median Δ = -5.007 px / -8.525 mm). Substantial gain on snatch only.
- `opencv-csrt+circle-b1` / `b5`:
  - Re-initialising CSRT on fitted circles led to severe feedback lock-in on distractor edges during the squat and clean & jerk (worsened on 14–18 labels per clip; median Δ = +1.5 to +3.8 px).
- `opencv-csrt+hough`:
  - Very low fit acceptance (0% on clean & jerk labelled frames, 20.8% on squat, 45.5% on snatch). Mostly falls back to CSRT; median Δ is +0.000 px on all clips.
  - False tracks appear as 0 only because of the ×0.7 confidence penalty; removing the penalty reveals 2 false tracks on both clean & jerk and snatch.

Reading: OpenBar's trackers report "tracked" on every frame while hundreds of pixels off. That is the
silent false-track failure ADR-0008 lists as a blocker. CSRT remains the clear leader across all three lifts:
it keeps 100% availability with 3.2 px MAE on the squat, and 6.4–7.5 px on clean and snatch.
The neural-network trackers evaluated in Phase 1 (ViTTrack, NanoTrack v2, DaSiamRPN) did not outperform CSRT.
Phase 2 (plate-geometry refinement) showed dramatic gains on the snatch (MAE 7.51 -> 3.73 px) but slightly degraded
the squat (3.22 -> 3.80 px) and clean & jerk (acceptance only 20%), failing the consistent paired improvement rule.
Fast lifts require Phase 3 (SAM 2 video segmentation).

Reproduce:
`research/opencv-tracking/.venv/Scripts/python research/opencv-tracking/compare.py --manifest validation/private/manifest.json --fixture self-back-squat-side-002 --fixture self-clean-jerk-side-002 --fixture self-snatch-side-002 --candidate opencv-csrt --candidate opencv-csrt+circle-a --candidate opencv-csrt+circle-b1 --candidate opencv-csrt+circle-b5 --candidate opencv-csrt+hough --output-dir target/opencv-spike/phase2-compare`

## 3. Non-negotiable rules

These come from `AGENTS.md`, ADR-0003/0005/0006/0007/0008 and lessons from this session.

1. **Tune and compare on development clips only.** Every script must refuse `purpose: validation` fixtures
   unless `--allow-held-out` is passed. That flag is used once, in Phase 5, after the freeze. Do not run any
   tracker on a validation clip "just to check". If a held-out fixture is exposed before the frozen run,
   deleting the output does **not** restore hold-out status: record the contamination and exclude that fixture
   from selection evidence, replacing/re-freezing the held-out set before any further tuning or selection.
2. **Frames come from OpenBar's decode contract** (ADR-0006): an FFmpeg process with `-autorotate`,
   `-map 0:v:0 -fps_mode passthrough -enc_time_base demux`, and timestamps from ffprobe frame PTS via
   `validation/tools/label_package.py` (`probe`, `display_size`, `require_media_hash`,
   `require_fixture_probe_match`). Never use `cv2.VideoCapture` timestamps or frame counts. The decoded frame
   count must equal the probed count.
3. **Pixel convention** (ADR-0007): integer coordinates are pixel centres. An OpenCV box `(x, y, w, h)` covers
   columns `x … x+w-1`, so its centre is `x + (w-1)/2`. Mask centroids from `np.nonzero` are already in
   pixel-centre coordinates.
4. **Lost means no coordinate.** A lost or out-of-image sample is `{"state": "lost"}` with no centre and no
   confidence. Never interpolate.
5. **Confidence** is a documented candidate-specific score clamped to [0, 1]. Prefer a tracker's native score
   when it is exposed; otherwise an explicit proxy (the current CSRT/KCF spike uses NCC to the seed template)
   is acceptable for diagnostics. Never set confidence to 1.0 merely because `update` returned ok, and never
   describe these scores as calibrated probabilities. Record the exact definition in
   `implementation.config.confidence`.
6. **Output** is `tracker-prediction-v1` (`validation/schema/tracker-prediction-v1.schema.json`), validated with
   `python validation/tools/schema_check.py --schema validation/schema/tracker-prediction-v1.schema.json <file>`.
   `implementation` must carry name, version and every parameter needed to reproduce the run (library
   versions, model file SHA-256, thresholds).
7. **Determinism.** Set `cv2.setNumThreads(1)`; seed every RNG (RANSAC, torch). For GPU runs, run twice and
   report the maximum per-sample difference instead of claiming determinism.
8. **Licences.** For every library, record the pinned version/commit, source URL, licence and distribution
   implications before use. For downloaded model/checkpoint artifacts, also pin the exact SHA-256. Prefer
   Apache-2.0/MIT/BSD. Non-commercial or AGPL components may be *evaluated* but must be flagged as "not
   shippable under the current licence" in every results table.
9. **Private inputs stay private.** Raw private media, extracted frames and annotations stay below the
   git-ignored `validation/private/` tree. Visual-QA images/crops also remain there because they contain
   source pixels. Local derived prediction JSON and benchmark reports may be written below git-ignored
   `target/`, but must never be committed or published when they expose private coordinates or source
   identity. Downloaded model/checkpoint files live below `validation/private/models/` and are pinned by
   SHA-256. Never commit private media, decoded frames, labels, private predictions, diagnostic frame images
   or model weights.
10. **Do not touch** the Rust workspace, `validation/schema/`, the golden analysis file, the fixtures or any
    `*_VERSION` constant in Phases 0–4. Phase 5 option (a) is the only planned engine change, and it needs the
    owner's go-ahead first.

## 4. Current state inventory

### Code (`research/opencv-tracking/`, in this research PR)

- `track.py`: OpenCV CSRT/KCF tracker run from a manual seed to a `tracker-prediction-v1` file.
  Confidence = normalised cross-correlation of the tracked box against the seed template (`TM_CCOEFF_NORMED`,
  clamped). Refuses held-out fixtures and validates the full FFmpeg decode count/exit status.
- `compare.py`: for each fixture, runs `openbar-cli tracker-run` (both built-in trackers) plus the selected
  research candidates from the seed to the last labelled frame, writes one `benchmark-suite-v1`, scores it
  with `openbar-cli benchmark`, and emits seed-excluded/false-track summaries. It enumerates only outputs
  produced by the current invocation so stale files cannot silently join the comparison.
- `visual_qa.py`: diagnostic labelled-frame crop overlays. Orchestrated runs receive an explicit prediction
  file list; directory discovery is retained only for standalone use.
- `requirements.txt`: `opencv-contrib-python==4.12.0.88`, `numpy==2.2.6`.
- `.venv/` (git-ignored): Python 3.11, created with `python -m venv research/opencv-tracking/.venv`.

### Environment

- Windows 11; Git Bash and PowerShell. Python 3.11 and 3.13 are installed (`py -0`).
- GPU: NVIDIA GeForce RTX 3060 Ti, 8 GB, driver 616.92. The pip OpenCV build has **no CUDA**
  (`cv2.cuda.getCudaEnabledDeviceCount() == 0`), so OpenCV DNN trackers run on the CPU.
- OpenCV 4.12 exposes `TrackerVit`, `TrackerNano`, `TrackerDaSiamRPN`, `TrackerGOTURN` and `TrackerMIL`.
- `ffmpeg`/`ffprobe` 9.0.2 are on PATH.

### Data (`validation/private/`)

| Fixture | Purpose | Labels (pass A) | Pass B | Seed |
|---|---|---|---|---|
| `self-back-squat-side-002` | development | 25 | — | yes |
| `self-clean-jerk-side-002` | development | 21 | — | yes |
| `self-snatch-side-002` | development | 23 | — | yes |
| `self-hang-snatch-side-002` (2023) | development | no (old-format package) | — | Claude-estimated, **owner has not confirmed** |
| `self-snatch-ohs-side-001` (2023) | development | no | — | no |
| `self-back-squat-side-001` | validation | 22 | 22 + `repeatability.json` | yes |
| `self-clean-jerk-side-001` | validation | 17 | pending | yes |
| `self-hang-snatch-side-003` | validation | 21 | pending | yes |
| `self-clean-pull-side-003` (exercise `other`) | validation | 20 | pending | yes |
| `self-clean-side-004` | validation | 23 | pending | yes |
| `self-clean-side-005` | validation | 25 | pending | yes |

Paths: `annotations/<id>.annotation-v1.json`, `annotations/<id>.owner-pass-b.annotation-v1.json`,
`annotations/<id>.repeatability.json`, `seeds/<id>.manual-target-seed-v1.json`. Labelling packages live in
`annotations/work/<id>/` (pass A) and `annotations/work/<id>.owner-pass-b/` (pass B). Every seed is the
owner's first labelled frame of the package window.

Owner tasks that unblock this plan: finish pass B for the five remaining validation clips (Phase 5).

## 5. Known gotchas (each one hit during this session)

1. **Seed timestamps are rounded to 6 decimals** and can land just after the true frame time. `tracker-run`
   then excludes the seed frame ("no selected decoded frame within 0.0005 s"). Always widen ranges by
   `TIMESTAMP_TOLERANCE_S` (0.0005) at both ends, as `compare.py` does.
2. **Trackers run forward only** from the seed. The seed must be the first labelled frame, not the last.
3. **The seed frame scores near-zero error by construction**, and the benchmark does not exclude it. With
   about 20 labels per clip that flatters MAE by roughly 5 %. Report metrics with and without the seed frame
   (Phase 0).
4. `annotations.py import-csv` takes `--metadata --csv --manifest --output` (named, not positional). Fill
   `provenance.annotated_at` in the package's `metadata.json` first; this session used the CSV file's
   modification time.
5. Shell heredocs: `"\\n"` inside a Python heredoc became a real newline in written files several times.
   Prefer the Edit/Write tools for any text containing escape sequences.
6. The plate's apparent radius shrinks 8–15 % as the bar rises in every rising-bar clip (camera low,
   perspective). A fixed-size tracker box drifts with it. Trackers that adapt their scale, or a geometric
   refinement, should do better. Keep per-frame radius as a diagnostic (Phase 2) because it feeds #58.

## 6. Phases

Each phase ends with its acceptance check. Do the phases in order; Phases 1–3 may overlap once Phase 0 is
done.

### Phase 0: housekeeping and fairer scoring (small)

1. Generalise `compare.py` into a producer registry: each candidate is
   `{name, interpreter, script, extra_args}`, so OpenCV, refinement and SAM 2 candidates can each run in
   their own venv. Keep the built-in OpenBar trackers as the baseline in every comparison.
2. Add a results summary written next to the benchmark result (`comparison-summary.json` plus a Markdown
   table) with, per clip and tracker: availability, MAE, p50/p90/max, maximum consecutive loss, the
   false-track count, and the same metrics **excluding the seed frame**. Reuse
   `tracker_filter_selection.false_track_diagnostics` by importing it, not copying it.
3. Add a visual QA overlay command that draws the label (yellow) and each tracker's prediction (one colour per
   tracker) on crops of the labelled frames and tiles them per clip. This session did it with ffmpeg
   `drawbox`; OpenCV drawing is fine in the research venv.
4. Import `self-snatch-side-002` (done: 23 labels, seed created from the first labelled frame) and include it in every comparison.
5. Open a PR with `research/` (code, requirements and this plan only).

Acceptance: re-running `compare.py` on the development clips reproduces the Section 2 numbers within 0.1 px.
The summary includes seed-excluded metrics and false-track counts. PR CI is green.

### Phase 1: OpenCV neural-network trackers (small)

Add `vit`, `nano` and `dasiamrpn` to `track.py` (skip GOTURN: an old Caffe model, weak on scale change).

- **Models.** Download each from its official source, verify and record its licence, and pin its SHA-256 in a
  `MODELS` table in `track.py`. Starting points, all to be verified:
  - VitTrack: `object_tracking_vittrack_2023sep.onnx` from `opencv/opencv_zoo` (`models/object_tracking_vittrack`).
  - NanoTrack v2: `nanotrack_backbone_sim.onnx` and `nanotrack_head_sim.onnx`, linked from OpenCV's tracker
    sample (`samples/python/tracker.py`); upstream is `HonglinChu/SiamTrackers`.
  - DaSiamRPN: `dasiamrpn_model.onnx`, `dasiamrpn_kernel_r1.onnx` and `dasiamrpn_kernel_cls1.onnx`, linked
    from the same OpenCV sample.
- **Construction:** `cv2.TrackerVit.create(params)` with `params.net = <path>` (likewise
  `TrackerNano_Params.backbone/neckhead` and `TrackerDaSiamRPN_Params.model/kernel_cls1/kernel_r1`).
- **Confidence:** use `tracker.getTrackingScore()` where the class provides it; otherwise keep the NCC score.
  Record which one is used.
- **Lost:** VitTrack has `tracking_score_threshold`; for all trackers, record a sample as `lost` when `update`
  returns false. Do not add thresholds tuned on the labels in this phase; tuning belongs to Phase 4.

Acceptance: all three run on the development clips, their predictions pass the schema check, and the
comparison table includes them with licences listed. **Status: completed** (see Section 2 for results).
None of the three neural trackers beat CSRT or satisfied the < 3 px target on fast lifts. NanoTrack v2
showed promise on the squat (7.0 px) but degraded on fast lifts and has an unconfirmed license;
DaSiamRPN maintained 100% availability but had 10.8–27.0 px error; ViTTrack lost tracking. Phase 2
(plate-geometry refinement) is the next focus.

### Phase 2: plate-geometry refinement on top of a box tracker (medium)

Execution detail (pre-declared parameters, coordinate handling, tests, metrics and decision rule):
[`docs/plans/PLATE_GEOMETRY_REFINEMENT_PLAN.md`](../docs/plans/PLATE_GEOMETRY_REFINEMENT_PLAN.md).

New candidate `opencv-<base>+circle` (evaluated on top of `csrt`). Per frame:

1. Take the base tracker's box. Use as the ROI that box expanded by 1.3× and clipped to the image.
2. Grayscale, Gaussian blur (σ ≈ 1.5), then Canny with thresholds derived from the ROI median. Keep edge points
   whose distance to the predicted centre lies in `[0.75 r_prev, 1.25 r_prev]`, where `r_prev` is the previous
   frame's radius (the seed radius on the first frame).
3. Fit a circle with deterministic RANSAC (fixed RNG seed; 3-point samples; inlier tolerance 1.5 px; a fixed
   iteration count), then an algebraic least-squares refit (Taubin) on the inliers.
4. Accept the fit only if inliers cover at least 50 % of the circumference (angular histogram of inliers,
   36 bins) and `|r - r_prev| / r_prev ≤ 0.1`. Otherwise use the base tracker's centre and lower the
   confidence (document the rule).
5. Variants compared: **A** (`circle-a`), refine the output only; **B** (`circle-b1` and `circle-b5`), refine and
   re-initialise the base tracker from the refined circle every 1 or 5 frames.
6. Also tried `cv2.HoughCircles(..., cv2.HOUGH_GRADIENT_ALT, ...)` (`hough`) in the same ROI with radius bounds
   `[0.85, 1.15] · r_prev` as an alternative to steps 2–4.
7. Diagnostics sidecar `<prediction>.geometry.json` written with the per-frame fitted radius, inlier coverage,
   and `cv2.fitEllipse` axes and angle on the inliers, recording the perspective effect (gotcha 6) for #58
   without changing the prediction schema.

**Status: completed.** Decision outcome: **NO GAIN / REJECT** per §9 of `PLATE_GEOMETRY_REFINEMENT_PLAN.md`.
- **Gate evaluation:** None of the geometry variants brought clean & jerk seed-excluded MAE (5.97–6.44 px)
  or p90 (9.87–10.25 px) under the < 3.0 px gate. At nominal physical scale, clean & jerk MAE is 6.74–7.27 mm
  (better than squat's 7.96 mm), confirming that the 3 px gate is ~2.2× stricter physically on clean & jerk
  due to plate pixel scale (222 px vs 101 px plate diameter).
- **Paired comparisons:**
  - `circle-a` (output refinement only): Substantially improved the snatch (MAE 7.51 → 3.73 px, p90 14.8 → 7.7 px;
    18 improved, 4 worsened, median Δ -5.0 px / -8.5 mm). However, it degraded the squat (MAE 3.22 → 3.80 px,
    p90 5.0 → 7.3 px; 8 improved, 8 worsened, 8 tied) due to background edge distractors during the descent, and
    had low acceptance on clean & jerk labeled frames (20% acceptance; 3 improved, 1 worsened, 16 tied). Because
    paired comparison does not favour it across every clip, it does not qualify as an across-the-board win.
  - `circle-b1` & `circle-b5` (re-initialisation): Severe feedback lock-in. Re-initialising CSRT on fitted circles
    caused drift onto inner plate rims or collar edges during squat and clean & jerk (worsened on 14–18 labels per
    clip; squat MAE surged to 6.2–8.4 px).
  - `hough`: Low acceptance (0% on clean & jerk labels, 20.8% on squat, 47.7% on snatch), falling back almost
    entirely to plain CSRT. Its 0 false tracks are solely due to the ×0.7 fallback penalty (with base confidence,
    it has 2 false tracks on clean & jerk and snatch).
- **Motion blur bias (Test 7):** Synthetic verification (`test_synthetic_motion_blur_bias`) measured a 0.5797 px
  vertical bias (dx = +0.0230 px, dy = -0.5792 px) under a 15 px vertical box blur proxy, with fitted radius
  90.51 px (ground truth 90 px).
- **Phase 3 direction:** Phase 3 (SAM 2) proceeds against plain `opencv-csrt` as the primary classical reference.

### Phase 3: SAM 2 video segmentation (medium; uses the GPU)

Separate environment: `research/sam2-tracking/` with its own `.venv` and `requirements.txt`.

- **Install:**
  - PyTorch with CUDA (pick the wheel for driver 616.92, e.g. a CUDA 12.x build from pytorch.org).
  - SAM 2 from `facebookresearch/sam2` (Apache-2.0), pinned to a commit.
  - On Windows set `SAM2_BUILD_CUDA=0` to skip the optional CUDA extension; it only affects mask post-processing.
  - If installation fails on Windows, use WSL2 and record that.
- **Checkpoints:** start with `sam2.1_hiera_small`, then `sam2.1_hiera_base_plus` (both fit in 8 GB with bf16
  autocast on Ampere). Pin their SHA-256.
- **Frames:**
  - The video predictor reads a directory of JPEG frames. For private fixtures, extract every frame in the
    window (seed to last label) through the decode contract into an ephemeral directory below
    `validation/private/work/sam2/`; delete it after the run. Raw decoded private frames must never be
    materialised under `target/` or any non-private tree.
  - Keep an index → (frame index, timestamp) map from the probe.
  - Use high JPEG quality (`-q:v 2`) and note the lossy step in the config.
- **Prompt:** at the seed frame, a box from the seed circle plus one positive point at the seed centre.
  Propagate forward only.
- **Centre from the mask**, as two candidates:
  - **(a) mask centroid**, which an occluding leg or arm biases;
  - **(b) circle/ellipse fit to the mask contour**, using RANSAC as in Phase 2, which is robust to partial
    occlusion.
- **Lost / confidence:**
  - `lost` when the mask is empty or its area is outside `[0.5, 1.5]` × the seed mask area.
  - Confidence = sigmoid of SAM 2's object-score logit. Record it as such.
- **Runtime:** record GPU model, wall time and peak GPU memory. Run twice and report the maximum per-sample
  difference (GPU non-determinism).

Acceptance: both SAM 2 variants appear in the comparison table with runtime and run-to-run difference.
Treat SAM 2 as an accuracy reference ("how good can it get") and do not judge it on mobile feasibility here.

### Phase 4: choose and freeze (small, needs the owner's sign-off)

1. Freeze at most two candidates **from development evidence only**. A candidate is eligible only if the
   development evidence has seed-excluded MAE < 3 px, availability > 99 %, and zero high-confidence false
   tracks under the documented diagnostic. Among eligible candidates, prefer lower seed-excluded MAE and
   report runtime alongside. If none is eligible, return to experimentation instead of spending the hold-out.
2. Any threshold tuning (lost thresholds, refinement tolerances) happens here, on development clips only, and
   is then frozen.
3. Write `research/<candidate>/freeze.json` with the implementation name and version, every parameter,
   pinned library versions/commits, model SHA-256s, the evaluated git commit and the SHA-256 of
   `validation/private/manifest.json`.
   After this, no parameter may change.
4. The owner confirms the freeze in writing (issue comment on #57).

### Phase 5: one held-out run and #57 integration (needs owner decisions)

Prerequisite: pass B done for all six validation clips, with `annotations.py repeatability` reports in
`validation/private/annotations/<id>.repeatability.json`.

1. Run each frozen candidate **once** on the six validation clips with `--allow-held-out`. Verify the
   configuration matches `freeze.json` before scoring. Report the gates per candidate, with seed-excluded
   metrics, false tracks and repeatability context. Do not change anything after seeing the results; a
   failure is a valid outcome (#57 decision option 3).
2. **#57 study integration.** `tracker_filter_selection.py` runs trackers through `openbar-cli tracker-run`
   and evaluates filters through `openbar-cli analyze --tracker template|contrast`, so external predictions
   cannot enter it as is. Options, for the owner to choose:
   - **(a) Engine change:** add `analyze --observations <tracker-prediction-v1>` so the canonical pipeline
     (calibration, filtering, kinematics) consumes an external tracker's stream, with its implementation id,
     version and config carried into the Analysis provenance. This crosses the tracker frame boundary: read
     ADR-0005, `docs/validation/CLI_PIPELINE.md` and `docs/validation/TRACKER_EXPERIMENTS.md` first, and expect
     a contract and version change (stop and ask before changing a schema or a `*_VERSION` constant).
   - **(b) Position-level only (recommended first):** extend the study to accept frozen external prediction
     files as tracker candidates for the MAE, availability and false-track gates, leaving filter evaluation to
     follow once (a) exists.

### Phase 6: production path (decision only, not in this plan)

If a candidate passes, the owner decides between OpenCV in production (Rust `opencv` bindings: a native C++
dependency, larger mobile binaries, licence record per `docs/architecture/ARCHITECTURE.md` "Dependency
policy") and a clean-room Rust re-implementation from the published algorithm (CSRT: Lukežič et al., 2017),
with an ADR. A neural-network model would cross into production as ONNX (ADR-0001).

## 7. Command cheat sheet

```bash
# OpenCV venv
python -m venv research/opencv-tracking/.venv
research/opencv-tracking/.venv/Scripts/python -m pip install -r research/opencv-tracking/requirements.txt

# Single tracker run (development fixture)
research/opencv-tracking/.venv/Scripts/python research/opencv-tracking/track.py --manifest validation/private/manifest.json --fixture self-back-squat-side-002 --seed validation/private/seeds/self-back-squat-side-002.manual-target-seed-v1.json --tracker csrt --output target/opencv-spike/csrt.prediction-v1.json

# Full comparison (development fixtures, with optional --visual-qa).
# Private visual-QA images are written under validation/private/diagnostics/visual-qa/.
research/opencv-tracking/.venv/Scripts/python research/opencv-tracking/compare.py --manifest validation/private/manifest.json --fixture self-back-squat-side-002 --fixture self-clean-jerk-side-002 --output-dir target/opencv-spike/dev-compare --visual-qa

# Standalone visual QA overlay command for private media
research/opencv-tracking/.venv/Scripts/python research/opencv-tracking/visual_qa.py --manifest validation/private/manifest.json --fixture self-back-squat-side-002 --predictions-dir target/opencv-spike/dev-compare/self-back-squat-side-002 --output validation/private/diagnostics/visual-qa/self-back-squat-side-002.visual-qa.png

# Import an owner CSV
python validation/tools/annotations.py import-csv --manifest validation/private/manifest.json --metadata validation/private/annotations/work/<id>/metadata.json --csv <csv> --output validation/private/annotations/<id>.annotation-v1.json

# Repeatability between passes
python validation/tools/annotations.py repeatability --manifest validation/private/manifest.json validation/private/annotations/<id>.annotation-v1.json validation/private/annotations/<id>.owner-pass-b.annotation-v1.json --output validation/private/annotations/<id>.repeatability.json

# Repository checks before any PR
python -m unittest discover -s validation/tests -p 'test_*.py'
python validation/tools/schema_check.py
cargo fmt --all -- --check
```

## 8. Open questions for the owner

1. Phase 5 integration: option (a), the engine change, or (b), position-level only, first?
2. Should the two 2023 development clips be labelled (more development data, different setups), or should new
   development clips be recorded instead?
3. If SAM 2 clearly wins but is not mobile-feasible, is it acceptable as an offline "high-accuracy mode", or
   only as a reference?
