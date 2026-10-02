# Plate-geometry refinement on CSRT (#57 Phase 2) — implementation plan

Status: accepted execution plan  
Related: #57, ADR-0005, ADR-0006, ADR-0007, ADR-0008, #58  
Parent plan: [`research/PLATE_TRACKING_PLAN.md`](../../research/PLATE_TRACKING_PLAN.md) §6 Phase 2.
That plan's §3 rules (development-only, decode contract, pixel convention, lost semantics, confidence,
output schema, determinism, licences, private inputs, do-not-touch list) apply unchanged and are not
repeated here.

## 1. Question

Does fitting the plate's outer rim inside the CSRT box bring CSRT under the #57 position gates on the
development clips, and in particular on the fast lifts?

#57 gates, from `validation/tools/tracker_filter_selection.py`:

| Gate | Target |
|---|---|
| Plate-centre MAE (seed-excluded) | < 3.0 px |
| Availability | > 99 % |
| High-confidence false tracks | 0 samples with confidence ≥ 0.8 and error > 3.0 px |

These are the Phase 4 eligibility gates in the parent plan. This phase does not change them.

## 2. Baseline and what it implies

CSRT, seed-excluded, development clips (parent plan §2), with the seed radius of each clip and the same
errors expressed in millimetres using a nominal 450 mm plate (`mm = px × 225 / r_seed_px`):

| Clip | Seed radius px | CSRT MAE px | CSRT MAE mm (nominal) | 3 px gate in mm | CSRT p90 px | False tracks |
|---|---:|---:|---:|---:|---:|---:|
| `self-back-squat-side-002` | 90.9 | 3.2 | 7.9 | 7.4 | 5.0 | 5 |
| `self-clean-jerk-side-002` | 199.2 | 6.4 | 7.2 | 3.4 | 10.2 | 2 |
| `self-snatch-side-002` | 132.1 | 7.5 | 12.8 | 5.1 | 10.3 | 3 |

Two consequences shape this plan:

1. **The pixel gate is not scale-invariant.** In physical terms CSRT is about as accurate on the clean &
   jerk as on the squat. The clean & jerk "fails" mostly because the plate is 2.2× larger in the frame, so
   3 px is a 2.2× stricter physical tolerance there. Only the snatch is clearly worse physically. This task
   keeps the pixel gate (changing it is an owner decision, §10) but reports mm-equivalent errors so the
   result can be read correctly.
2. **The labels set a noise floor.** Two owner passes on `self-back-squat-side-001` disagree by 2.06 px
   mean (2.45 px RMSE, 5.9 px max). Treating that as the disagreement of two independent labels, one label
   is about 1.5 px mean from the truth. Under that model a perfect tracker would score about 1.5 px MAE and
   about 2.5 px p90 against single-pass labels at squat scale. Label noise on the clean & jerk, where the
   plate is twice as large, has not been measured and may be larger in pixels. A p90 < 3 px target on
   ~20 labels is therefore close to the noise floor, and with 20 seed-excluded labels the nearest-rank p90
   is the 18th-smallest error, so one label moves it.

Hence: the **decision** uses the three #57 gates (seed-excluded). p90, max and the paired comparison in
§7 are diagnostics. The original task's "clean & jerk p90 < 3 px" is reported, not used as the pass rule.

## 3. Scope

In scope: `research/opencv-tracking/` only (new `refine_circle.py`, changes to `track.py` and
`compare.py`, research-only tests), plus updates to §2 and §6 of `research/PLATE_TRACKING_PLAN.md`.

Out of scope: production crates, `validation/schema/`, fixtures, `*_VERSION` constants, validation
clips, any base tracker other than CSRT (Phase 1 found none better), gradient-direction edge filtering,
SAM 2 (Phase 3), and threshold tuning (Phase 4).

## 4. Pre-declared parameters

Every value below is fixed **before** any variant is scored against labels, and is recorded in
`implementation.config`. Changing any of them after seeing label errors is Phase 4 tuning and must be
reported as such, not folded silently into Phase 2 results.

| Parameter | Value | Rationale |
|---|---|---|
| ROI expansion | 1.3 × base box, about the box centre | parent plan |
| Gaussian blur | `ksize=(0, 0)`, σ = 1.5 (OpenCV derives the kernel) | avoids a σ/kernel mismatch |
| Canny thresholds | `lower = max(0, 0.66·v)`, `upper = min(255, 1.33·v)`, `v` = ROI grey median | parent plan |
| Annulus | `d ∈ [0.75, 1.25] · r_prev` about the base-box centre | parent plan; half-width 0.25 r ≥ 22 px on these clips, well above CSRT error |
| RANSAC iterations | 1000, fixed | 3-point samples; ≥ 99.9 % success down to ~19 % inlier ratio (100 iterations only reaches ~55 % at 20 %) |
| RANSAC RNG | `np.random.default_rng([42, frame_index])`, new generator per frame | frame results independent of earlier frames' draw counts |
| Degenerate sample | skip if the 3 points are near-collinear (circumradius > 10 · r_prev) | |
| Inlier tolerance | 1.5 px radial residual | parent plan |
| Refit | Taubin algebraic fit on inliers, then inliers recomputed against the refit circle | Kåsa underestimates radius on partial arcs |
| Coverage | inliers of the refit circle occupy ≥ 18 of 36 × 10° angular bins about the refit centre | parent plan |
| Frame-to-frame radius | `|r − r_prev| / r_prev ≤ 0.10` | parent plan |
| Seed-anchored radius | `|r − r_seed| / r_seed ≤ 0.20` | new: stops `r_prev` random-walking onto an inner rim or hub; covers the 8–15 % perspective shrink (parent gotcha 6) and the 3–12 % per-frame diameter spread from P3.0 |
| Rejected-fit confidence | base confidence × 0.7 | parent plan; see §5.4 |
| Hough (variant H) | `HOUGH_GRADIENT_ALT`, `dp=1.5`, `minDist=r_prev`, `param1=300`, `param2=0.9`, `minRadius=floor(0.85·r_prev)`, `maxRadius=ceil(1.15·r_prev)` | values the OpenCV `HoughCircles` documentation suggests for ALT; fixed now, not tuned |
| B re-init period | N ∈ {1, 5} frames | parent plan |

## 5. Algorithm (`refine_circle.py`)

Pure functions on a decoded BGR frame and a base box. No I/O and no global state, so they can be tested
on synthetic images.

### 5.1 Coordinates (ADR-0007)

- Base box `(x, y, w, h)` from CSRT has centre `(x + (w − 1)/2, y + (h − 1)/2)`.
- ROI: half-extents `1.3·w/2`, `1.3·h/2` about that centre; integer slice bounds
  `x0 = max(0, floor(cx − 0.65·w))`, `x1 = min(width, ceil(cx + 0.65·w) + 1)` (same for y). Slices are
  half-open, so the ROI never indexes outside the frame.
- An edge pixel at ROI array index `(row, col)` is at full-frame pixel-centre coordinate
  `(x0 + col, y0 + row)`. No `+0.5`. All fitting happens in full-frame coordinates.

A half-pixel error here would use up a sixth of the 3 px budget silently, so §6 tests it directly.

### 5.2 Per-frame steps

1. If the base tracker reports lost or an invalid box, emit `lost`. Refinement never revives a lost frame;
   doing so would make it a detector, which is out of scope.
2. Grey ROI → Gaussian blur → Canny with median-derived thresholds.
3. Keep edge points whose distance to the base-box centre lies in the annulus.
4. RANSAC (parameters in §4) → Taubin refit on inliers → recompute inliers.
5. Accept only if coverage, frame-to-frame radius and seed-anchored radius all pass, and the refit centre is
   inside the frame.
6. On accept: output centre = refit centre, `r_prev ← r`. On reject: output centre = base-box centre,
   `r_prev` unchanged.

The seed frame is not refined: it emits the seed exactly as the baseline does, and `r_prev = r_seed` for the
first tracked frame.

### 5.3 Variants

| Name | Behaviour |
|---|---|
| `opencv-csrt+circle-a` | CSRT runs untouched; refinement only changes the emitted centre. |
| `opencv-csrt+circle-b1` | After an **accepted** fit, re-initialise CSRT on the fitted circle every frame. |
| `opencv-csrt+circle-b5` | As B1, but re-initialise only on frames where `frame_offset_from_seed % 5 == 0` and the fit was accepted. |
| `opencv-csrt+hough` | Variant A with steps 2–4 replaced by Hough (§4). Among returned circles, pick the one whose centre is nearest the base-box centre; apply the same radius checks and fallback. |

Re-initialisation box (B variants): `seed_box(cx, cy, r, width, height)` from `track.py`, reused rather
than reimplemented. CSRT is then initialised exactly as at the seed (circle bounding box, clipped to the
image), and the box's ADR-0007 centre lies within 0.5 px of the fit except where clipping applies. The emitted centre is always the fitted circle centre, never the quantised box
centre. Rejected fits never re-initialise.

Known risk to report, not to fix here: feedback can lock in a wrong circle (inner rim, a plate behind),
which variant A cannot do.

### 5.4 Confidence

- Accepted fit: NCC of the **seed** template at a box centred on the refined centre (same definition and
  template as the CSRT baseline, so values are comparable across variants).
- Rejected fit: base confidence × 0.7.
- Record both rules in `implementation.config.confidence`.

The × 0.7 penalty pushes most rejected frames below the 0.8 false-track threshold by construction. It can
lower the false-track count without improving accuracy, so §7 also reports errors regardless of confidence.

### 5.5 Geometry sidecar

For each prediction `<fixture>.<name>.prediction-v1.json`, write `<fixture>.<name>.geometry.json` in the
same directory. The name must not end in `.prediction-v1.json`, so `compare.py` and `visual_qa.py` never
pick it up as a prediction. Contents:

- a header: `format: "openbar-research-geometry-sidecar"`, `format_version: 0`, the prediction's
  `implementation` block and `fixture_id`;
- per sample: `timestamp_s`, `fit_attempted`, `accepted`, `reject_reasons` (list), `radius_px`, `inlier_count`,
  `edge_count`, `coverage_bins`, `canny_lower`, `canny_upper`, and `cv2.fitEllipse` on the inliers
  (`major_px`, `minor_px`, `angle_deg`; null when there are fewer than 5 inliers, and always null for Hough).

It is research output and is not committed (it is derived from private media), and it gets no schema.
Ellipse elongation mixes perspective foreshortening and motion blur. Record both as possible causes and do
not attribute them to perspective alone (P3.0 found the cause unidentifiable without a physical reference).
The dev labels cannot validate the fitted radius: their `target_size_px` is the seed radius carried forward
on every frame.

## 6. Tests before scoring (research venv, not CI)

`research/opencv-tracking/tests/test_refine_circle.py`, `unittest`, run with the research venv (CI's Python
is stdlib-only, so these tests do not belong under `validation/tests/`). Synthetic frames: an anti-aliased
disk rendered by 8× supersampling with a known sub-pixel centre, on a textured background.

1. Clean disk, r ∈ {60, 130, 200}: centre error < 0.1 px, radius error < 0.2 px.
2. ROI touching each frame edge: the same accuracy and no indexing error (catches §5.1 offset bugs).
3. Base box offset by 0.2 r from the true centre: still converges.
4. Disk with a 60 % arc occluded: rejected on coverage, base centre emitted, confidence × 0.7.
5. Concentric inner ring at 0.5 r plus outer rim: picks the outer rim.
6. A radius jump of 15 % versus `r_prev`: rejected on continuity.
7. Vertical box blur of 15 px (motion-blur proxy): record the centre bias. This is a measurement, not a
   pass/fail, and the number goes into the results note.
8. Determinism: the same frame and box twice → identical output; frame results independent of call order.
9. Hough on test 1: centre within 1 px (Hough is quantised; record the actual value).

## 7. Evaluation

Run once, in one `compare.py` invocation, on `self-back-squat-side-002`, `self-clean-jerk-side-002` and
`self-snatch-side-002`, with `opencv-csrt` and both OpenBar baselines alongside the four variants.

Report per clip and candidate, both all-frames and seed-excluded (existing summary), plus:

- **mm-equivalent** MAE/p90 (nominal 450 mm plate, labelled as nominal);
- **high-error samples regardless of confidence** (error > 3 px), next to the false-track count;
- **false tracks with the × 0.7 penalty removed** (i.e. with base confidence), to show how much of any
  false-track improvement comes from the penalty;
- **paired comparison against CSRT on the same labels**: number of labels improved/worsened and the median
  per-label error difference. With about 20 labels this is far more sensitive than comparing two MAEs;
- **fit acceptance rate** per clip, from the sidecar, overall and on labelled frames;
- **runtime** (`processing_wall_s`) and the overhead relative to CSRT.

Interpret a variant as improving on CSRT only if the paired comparison favours it on every clip. MAE
differences under ~0.5 px are within label noise and must not drive a decision.

## 8. Acceptance

1. §6 tests pass in the research venv (command and exit code reported).
2. Re-running `compare.py` reproduces the CSRT baseline in parent §2 within 0.1 px (unchanged baseline).
3. Two consecutive `compare.py` runs produce identical prediction and sidecar files apart from `runtime`.
4. Every new prediction passes
   `python validation/tools/schema_check.py --schema validation/schema/tracker-prediction-v1.schema.json <file>`
   (the no-argument form only checks committed fixtures and would not cover these files).
5. No validation fixture was run (`track.py` refuses them without `--allow-held-out`; that flag is not used).
6. Parent plan updated:
   - §2: both tables gain the four variants (licence: Apache-2.0, OpenCV + NumPy BSD), plus the
     mm-equivalent and paired-comparison summary;
   - §6 Phase 2: status, the decision in §9 with its evidence, the measured blur bias from test 7, and an
     explicit statement on whether the clean & jerk seed-excluded MAE and p90 are below 3 px.
7. Nothing private is committed: predictions, sidecars and visual QA stay under `target/` or
   `validation/private/`. Committed results are aggregates only, as in parent §2.

## 9. Decision outcomes (pre-registered)

| Outcome | Condition | Next step |
|---|---|---|
| **CANDIDATE** | A variant meets all three gates on all three clips, seed-excluded | Freeze candidate in Phase 4 (owner sign-off); no parameter changes before the freeze. |
| **PARTIAL** | Paired comparison favours a variant on every clip, but a gate still fails | Keep the variant as the Phase 3 comparison base; record which gate fails and by how much in px and mm. |
| **NO GAIN** | No consistent paired improvement | Record REJECT for geometry refinement on CSRT; Phase 3 (SAM 2) proceeds against plain CSRT. |

Any outcome where the false-track gate passes only because of the × 0.7 penalty is reported as such and
does not count as passing that gate on its own.

## 10. Questions for the owner (do not resolve in this task)

1. Should #57's position gate stay in pixels, or move to a size-normalised form (fraction of plate radius,
   or mm via the plate diameter)? §2 shows the pixel gate is 2.2× stricter physically on the clean & jerk
   than on the squat.
2. Should pass B be labelled on one development fast lift so the label noise floor at large plate scale is
   known before Phase 4?

## 11. Commands

```bash
# Research tests
research/opencv-tracking/.venv/Scripts/python -m unittest discover -v -s research/opencv-tracking/tests -p 'test_*.py'

# Development comparison
research/opencv-tracking/.venv/Scripts/python research/opencv-tracking/compare.py --manifest validation/private/manifest.json --fixture self-back-squat-side-002 --fixture self-clean-jerk-side-002 --fixture self-snatch-side-002 --candidate opencv-csrt --candidate opencv-csrt+circle-a --candidate opencv-csrt+circle-b1 --candidate opencv-csrt+circle-b5 --candidate opencv-csrt+hough --output-dir target/opencv-spike/phase2-compare

# Schema check per prediction
python validation/tools/schema_check.py --schema validation/schema/tracker-prediction-v1.schema.json target/opencv-spike/phase2-compare/<fixture>/<fixture>.<candidate>.prediction-v1.json

# Repository checks before the PR
python -m unittest discover -s validation/tests -p 'test_*.py'
python validation/tools/schema_check.py
```
