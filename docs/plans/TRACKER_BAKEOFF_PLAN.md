# Tracker bake-off (#57 Phase 3) — implementation plan

Status: approved
Related: #57, ADR-0001, ADR-0005, ADR-0006, ADR-0007, ADR-0008  
Parent plan: [`research/PLATE_TRACKING_PLAN.md`](../../research/PLATE_TRACKING_PLAN.md) §6 Phase 3.
That plan's §3 rules apply unchanged and are not repeated here: development clips only, the decode contract,
the pixel convention, lost semantics, confidence, the output schema, determinism, licences, private inputs and
the do-not-touch list.

## 1. Question

Does any optical-flow, video-segmentation or neural point-tracking approach meet the #57 position gates on the
development clips, where CSRT (Phase 1) and CSRT with circle refinement (Phase 2) did not?

The approaches are compared in one PR and one `compare.py` run, against the same labels and the same CSRT
baseline. The gates are unchanged from the Phase 2 plan §1:

| Gate | Target |
|---|---|
| Plate-centre MAE (seed-excluded) | < 3.0 px |
| Availability | > 99 % |
| High-confidence false tracks | 0 samples with confidence ≥ 0.8 and error > 3.0 px |

Baseline to beat (`opencv-csrt`, seed-excluded): MAE 3.22 / 6.44 / 7.51 px and p90 5.00 / 10.25 / 10.31 px on
squat / clean & jerk / snatch. Errors are also reported in nominal mm, as in Phase 2, because the 3 px gate is
about 2.2× stricter physically on the clean & jerk.

## 2. Roster

The roster is fixed before anything is scored. No candidate is added, removed or re-parameterised after seeing
label errors. A candidate that cannot be installed or run is reported as **NOT EVALUATED** with the reason, and
does not block the rest.

| # | Candidate | Family | Environment | Code / weights licence | Shippable |
|---|---|---|---|---|---|
| 1 | `opencv-lk-affine` | optical flow, plate-face points | OpenCV venv | Apache-2.0 | yes |
| 2 | `opencv-csrt+lk` | as 1, CSRT fallback | OpenCV venv | Apache-2.0 | yes |
| 3 | `sam2.1-small-centroid` | video segmentation | GPU venv | Apache-2.0 / Apache-2.0 | yes |
| 4 | `sam2.1-small-circle` | video segmentation | GPU venv | Apache-2.0 / Apache-2.0 | yes |
| 5 | `sam2.1-bplus-centroid` | video segmentation | GPU venv | Apache-2.0 / Apache-2.0 | yes |
| 6 | `sam2.1-bplus-circle` | video segmentation | GPU venv | Apache-2.0 / Apache-2.0 | yes |
| 7 | `cutie-base-centroid` | video segmentation | GPU venv | MIT / not stated separately | not without confirmation |
| 8 | `cutie-base-circle` | video segmentation | GPU venv | MIT / not stated separately | not without confirmation |
| 9 | `bootstapir-affine` | neural point tracking | GPU venv | Apache-2.0 / Apache-2.0 | yes |
| 10 | `cotracker3-affine` | neural point tracking | GPU venv | CC-BY-NC-4.0 / CC-BY-NC-4.0 | **no** (evaluation only) |

Alongside: `opencv-csrt`, `template-sad-v1` and `local-contrast-centroid-v1`, unchanged.

Sources and licence evidence (verified 2026-10-02). The Phase 3 bootstrap `requirements.txt` did not retain
an immutable package/VCS lock, despite the original wording here. After review, the runner verifies checkpoint
SHA-256 values and records installed package versions plus pip VCS direct-source metadata when available.
Historical Run 1 therefore remains development/exploratory evidence; shortlisted candidates must be rerun under
an explicit retained environment lock before any Phase 4 freeze.

| Library | Source | Licence evidence |
|---|---|---|
| SAM 2.1 | `github.com/facebookresearch/sam2` | repo `LICENSE` Apache-2.0, covering code and checkpoints |
| Cutie | `github.com/hkchengrex/Cutie` (last commit `ec5cdd4cf16f`, 2024-11-08) | repo `LICENSE` MIT. The `cutie-base-mega.pth` release asset has no separate licence statement |
| TAPNet (BootsTAPIR) | `github.com/google-deepmind/tapnet` | README: code Apache-2.0, and "all pre-trained model checkpoints … are also licensed under Apache 2.0" |
| CoTracker3 | `github.com/facebookresearch/co-tracker` | `LICENSE.md`: Attribution-NonCommercial 4.0 International |
| PyTorch, torchvision | `pytorch.org` | BSD-3-Clause |

Every results table carries the licence column. Cutie is labelled like `opencv-nano` in Phase 1: unconfirmed,
not shippable without confirmation.

Considered and excluded: plate detectors such as YOLO (automatic detection is out of M0 scope, and often
AGPL), and trackers already rejected in Phases 1–2.

## 3. Scope

In scope:

- `research/opencv-tracking/`:
  - `point_motion.py`, a new pure module (§5.1);
  - a pure refactor of `refine_circle.py` that extracts the inline RANSAC + Taubin fit into
    `fit_circle_ransac(points, r_ref, frame_index)`, with Phase 2 behaviour byte-identical;
  - candidates 1–2 in `track.py`;
  - registry, licence and GPU-runtime columns in `compare.py`;
  - research tests.
- `research/gpu-tracking/` (new): its own `.venv`, `requirements.txt`, `track_gpu.py` (one CLI for candidates
  3–10, matching `track.py`'s arguments), `centres.py` (pure centre extraction, §5.3), `download_models.py`
  (SHA-256-pinned) and tests.
- `research/PLATE_TRACKING_PLAN.md`: §2 tables, §4 inventory and §6 Phase 3 status.

Out of scope: production crates, `validation/schema/`, fixtures, `*_VERSION` constants, validation clips,
threshold tuning (Phase 4), cropping or zooming around the plate, and any mobile-feasibility judgement.
Runtime is reported, not judged.

## 4. Shared inputs and rules

- **Seed:** the manual seed circle `(cx, cy, r_seed)` at the first labelled frame, as in Phases 1–2. The seed
  frame emits the seed exactly. Tracking runs forward only, from the seed to `end_s`.
- **Frames for GPU candidates:** decoded once per run through the decode contract (ADR-0006, `label_package`),
  then written as JPEG `-q:v 2` to a fresh `validation/private/work/gpu-tracking/<fixture>-<candidate>/`
  directory. That directory is deleted in a `finally` block, including on error. All GPU candidates read the
  same JPEGs, so input differences don't confound model differences. The lossy step is recorded in
  `implementation.config`. OpenCV candidates keep the raw decode used in Phases 1–2.
- **Index map:** JPEG index → (frame index, timestamp) comes from the probe. Timestamps in predictions are
  probe timestamps, never index × nominal FPS.
- **Coordinates:** every library's output convention is pinned and converted to ADR-0007 pixel centres in one
  tested function per library (§6). Resizing from model resolution back to the frame uses
  `x_frame = (x_model + 0.5) · W / w_model − 0.5` only when the library's coordinates are pixel-centre based.
  Otherwise the conversion is derived and tested.
- **Lost:** emitted as `{"state": "lost"}` with no centre and no confidence. Nothing revives a lost track except
  the explicit CSRT fallback in candidate 2.
- **Determinism:** `cv2.setNumThreads(1)`, `cv2.setRNGSeed(42)` before each frame's RANSAC, `torch.manual_seed(0)`,
  `torch.backends.cudnn.benchmark = False`. GPU candidates are run twice and the maximum per-sample centre
  difference is reported. Determinism is not claimed.
- **Runtime:** wall time per run. For GPU runs, also record the GPU model, driver and
  `torch.cuda.max_memory_allocated`.

## 5. Candidates and pre-declared parameters

Everything below is fixed now and recorded in `implementation.config`.

### 5.1 Point-motion candidates (1, 2, 9, 10)

All four share one query set and one centre estimator. That way the comparison isolates the point tracker.

- **Query points:** `cv2.goodFeaturesToTrack` on the grey seed frame, restricted to the annulus
  `[0.25, 0.90] · r_seed` about the seed centre. This keeps the hub and the rim edge out.
  Parameters: `maxCorners=200`, `qualityLevel=0.01`, `minDistance=5`, `blockSize=7`. Rim points are avoided on
  purpose: along a smooth edge, only motion across the edge is measurable (the aperture problem).
- **Centre:** `cv2.estimateAffinePartial2D(seed_pts[v], cur_pts[v], method=cv2.RANSAC,
  ransacReprojThreshold=1.5, maxIters=2000, confidence=0.999, refineIters=10)` over the visible points `v`.
  The fitted similarity maps the seed centre to the current centre. The estimate is seed → current, not chained
  frame to frame, so transform errors don't compound.
- **Lost:** fewer than 12 RANSAC inliers, or the centre outside the frame.
- **Confidence:** RANSAC inliers / initial query count, clamped to [0, 1]. This is a geometric support score,
  not a probability.

Per candidate:

| Candidate | Point tracker | Visibility |
|---|---|---|
| `opencv-lk-affine` | `cv2.calcOpticalFlowPyrLK` frame to frame, `winSize=(21, 21)`, `maxLevel=3`, criteria `(COUNT \| EPS, 30, 0.01)`. A point is dropped permanently when the forward–backward error exceeds 1.0 px or LK status is 0 | surviving points |
| `opencv-csrt+lk` | as above, with CSRT running in parallel. When LK is lost, emit the CSRT box centre with CSRT confidence × 0.7 (the Phase 2 rule) | as above |
| `bootstapir-affine` | BootsTAPIR PyTorch, offline, `bootstapir_checkpoint_v2.pt`, input resized to 512 × 512. If that exceeds 8 GB, run at 256 × 256 and record the fallback | the model's visibility, using the threshold from the TAPNet PyTorch demo |
| `cotracker3-affine` | CoTracker3 offline, `scaled_offline.pth`, default internal resolution | the model's visibility output at its default threshold |

Known risk, reported rather than fixed here: models that run at reduced resolution lose precision. At 512 px
across a frame over 1000 px wide, one model pixel is more than 2 frame pixels.

### 5.2 Mask candidates (3–8)

| Candidate family | Initialisation | Mask | Confidence |
|---|---|---|---|
| SAM 2.1 `small` (`sam2.1_hiera_small.pt`, `sam2.1_hiera_s.yaml`) and `base_plus` (`sam2.1_hiera_base_plus.pt`, `sam2.1_hiera_b+.yaml`) | seed frame: box = seed circle bounding box, plus one positive point at the seed centre | logits > 0, bf16 autocast, `SAM2_BUILD_CUDA=0` on Windows | sigmoid of SAM 2's object-score logit |
| Cutie `cutie-base-mega.pth`, default evaluation config | seed frame: rasterised seed disk; pixel `(i, j)` is in the disk when `(i − cx)² + (j − cy)² ≤ r_seed²` | argmax of the object-vs-background probability | mean foreground probability over the mask pixels |

**Lost:** the mask is empty, or its area is outside `[0.5, 1.5]` × the seed-frame mask area. For SAM 2 that is
the model's own mask on the seed frame. For Cutie it is the rasterised disk.

### 5.3 Centre from a mask (`centres.py`)

- **`-centroid`:** the mean of the mask pixels' `(col, row)`, which are already ADR-0007 pixel centres. An
  occluding arm or leg biases it, by design. This shows how large that bias is.
- **`-circle`:** take the largest connected component, `cv2.findContours(..., CHAIN_APPROX_NONE)`, then
  `fit_circle_ransac` with the Phase 2 settings: 1.5 px inlier tolerance, 1000 iterations, RNG
  `[42, frame_index]`, Taubin refit. Accept the fit when coverage is at least 18 / 36 bins and
  `|r − r_seed| / r_seed ≤ 0.20`. On reject, emit the centroid with confidence × 0.7. Occluder edges on the mask
  boundary are what RANSAC is there to discard.

## 6. Tests before scoring (research venvs, not CI)

OpenCV venv (`research/opencv-tracking/tests/`):

1. `refine_circle` regression: the 9 Phase 2 tests still pass after the refactor, and a re-run of `csrt+circle-a`
   on the squat produces a byte-identical prediction apart from `runtime`.
2. `fit_circle_ransac` on synthetic points: a full circle and a 50 % arc with 30 % outliers recover the centre
   within 0.1 px.
3. `point_motion`: a textured synthetic disk translated by sub-pixel amounts and rotated by up to 10° per frame
   over 30 frames gives a centre error under 0.2 px.
4. `point_motion` with half of the points occluded still converges. Below 12 inliers it emits lost.
5. `point_motion` determinism: two runs produce identical output.

GPU venv (`research/gpu-tracking/tests/`):

6. `centres`: on a disk rendered at a sub-pixel centre, centroid and circle are within 0.1 px. With a bar across
   30 % of the disk, the circle result stays within 0.5 px and the centroid bias is recorded. Empty or oversized
   masks are lost.
7. Seed-disk rasterisation follows the pixel-centre rule (symmetric about a half-integer centre).
8. Coordinate conversion per library: a round trip from frame to model resolution and back is within 1e-9 px.
9. **GPU smoke test on a synthetic video:** a textured disk moving 2 px per frame over 30 frames, run through
   every GPU candidate. Mask candidates must be within 0.5 px and point candidates within 1.0 px of the truth.
   A consistent ±0.5 px offset means a convention bug, and must be fixed before scoring.

## 7. Evaluation

One `compare.py` run on `self-back-squat-side-002`, `self-clean-jerk-side-002` and `self-snatch-side-002`, with
all ten candidates and the three baselines. Then a second identical run to measure GPU run-to-run differences.

Reported per clip and candidate, as in Phase 2:

- seed-excluded MAE, p50, p90 and max in px and nominal mm, plus availability;
- false tracks, with and without the × 0.7 fallback penalty;
- high-error samples regardless of confidence;
- paired comparison against CSRT: improved, worsened, tied and median Δ;
- for `-circle` candidates, the fit acceptance rate;
- runtime and overhead against CSRT, and for GPU runs the peak memory and maximum run-to-run difference.

A candidate counts as improving on CSRT only if the paired comparison favours it on every clip. MAE differences
under about 0.5 px are within label noise.

**Multiple comparisons:** ten candidates on 66 labels make it likely that one looks good by chance. That is why
the Phase 5 held-out run, and not this table, is the evidence for shipping.

## 8. Acceptance

1. All §6 tests pass in their venvs, with the commands and exit codes reported.
2. `opencv-csrt` reproduces the Phase 2 baseline within 0.1 px.
3. OpenCV candidates produce identical predictions across the two runs, apart from `runtime`. GPU candidates
   report their maximum difference.
4. Every new prediction passes `python validation/tools/schema_check.py --schema
   validation/schema/tracker-prediction-v1.schema.json <file>`.
5. No validation fixture is run (`--allow-held-out` is not used).
6. No decoded frames remain under `validation/private/work/gpu-tracking/` after the runs, and none were ever
   written outside `validation/private/`.
7. Model files live under `validation/private/models/`, pinned by SHA-256. No weights are committed.
8. The parent plan is updated with the results, the licence column, every NOT EVALUATED reason and the §9
   outcome. Only aggregates are committed.

## 9. Decision outcomes (pre-registered)

| Outcome | Condition | Next step |
|---|---|---|
| **CANDIDATE** | One or more candidates meet all three gates on all three clips, seed-excluded | Phase 4: freeze at most two, preferring lower MAE, with licence and runtime stated. Needs owner sign-off; no parameter changes before the freeze. |
| **PARTIAL** | The paired comparison favours a candidate on every clip, but a gate still fails | Record which gate fails, by how much in px and mm, and which family it belongs to. The owner decides between targeted follow-up (for example cropping for point trackers) and the gate question in §10. |
| **NO GAIN** | No candidate shows a consistent paired improvement | Record REJECT for each family. CSRT remains the reference, and the owner decides whether #57 needs more data, a different gate or a different approach. |

A false-track gate passed only because of the × 0.7 penalty is reported as such and does not count. A
non-shippable candidate (CoTracker3, or Cutie until its weights are confirmed) can reach CANDIDATE as an
accuracy reference, but cannot be frozen for shipping without a licence resolution.

## 10. Questions for the owner (do not resolve in this task)

1. Should the #57 position gate stay in pixels, or become size-normalised (fraction of plate radius, or mm)?
   Carried over from Phase 2 §10.
2. Should a second labelling pass (pass B) be done on one fast lift, to measure label noise at large plate scale
   before Phase 4?
3. If a GPU-only candidate wins, is it acceptable as an offline "high-accuracy mode", or only as a reference?
   This is parent plan §8 question 3.

## 11. Commands

```bash
# GPU venv bootstrap (CUDA 12.x PyTorch wheel matching driver 616.92).
# requirements.txt is not the historical run lock; prediction provenance captures the installed environment.
python -m venv research/gpu-tracking/.venv
research/gpu-tracking/.venv/Scripts/python -m pip install -r research/gpu-tracking/requirements.txt
research/gpu-tracking/.venv/Scripts/python research/gpu-tracking/download_models.py

# Tests
research/opencv-tracking/.venv/Scripts/python -m unittest discover -s research/opencv-tracking/tests
research/gpu-tracking/.venv/Scripts/python -m unittest discover -s research/gpu-tracking/tests

# Bake-off (run twice; the second run only measures run-to-run differences)
research/opencv-tracking/.venv/Scripts/python research/opencv-tracking/compare.py --manifest validation/private/manifest.json --fixture self-back-squat-side-002 --fixture self-clean-jerk-side-002 --fixture self-snatch-side-002 --candidate opencv-csrt --candidate opencv-lk-affine --candidate opencv-csrt+lk --candidate sam2.1-small-centroid --candidate sam2.1-small-circle --candidate sam2.1-bplus-centroid --candidate sam2.1-bplus-circle --candidate cutie-base-centroid --candidate cutie-base-circle --candidate bootstapir-affine --candidate cotracker3-affine --output-dir target/opencv-spike/phase3-bakeoff
```
