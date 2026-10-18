# Full-resolution sub-pixel rim circle refinement: development result (#249, #233, #57, #88)

Date: 2026-10-18. Research only, development clips only. Nothing here is a frozen or held-out result, and no
number below generalises beyond these clips. Parent:
[PLATE_DETECTION_SAM3_SEED_RESULTS.md](PLATE_DETECTION_SAM3_SEED_RESULTS.md),
[PLATE_DETECTION_PHASE_B_PLAN.md](../plans/PLATE_DETECTION_PHASE_B_PLAN.md). Code in
`research/plate-detection/rim_refine.py`; unit tests in `research/plate-detection/tests/test_rim_refine.py`.

## Questions

1. Can a deterministic radial-gradient circle fit (`rim-subpixel-v1`) on the full-resolution decoded frame
   recover sub-pixel rim scale when initialized from a coarse bounding-box circle (`geometry.circle_from_box`,
   as emitted by Phase B box student detectors) or a 256×256 upscaled tracker mask (`geometry.circle_from_mask`)?
2. Does per-frame radial-gradient refinement improve upon `geometry.circle_from_mask` during mid-lift motion,
   or is its benefit concentrated on stationary frame-0 box priors and specific high-contrast clips?

## Method: `rim-subpixel-v1` (`research/plate-detection/rim_refine.py`)

Given a full-resolution BGR or grayscale frame and a coarse prior circle `(cx0, cy0, r0)` in ADR-0007
pixel-centre coordinates:

1. Convert the image to `float64` luma (`0.114 B + 0.587 G + 0.299 R`) and apply a separable zero-phase
   `[0.25, 0.5, 0.25]` binomial smoothing kernel along X and Y.
2. Sample 72 radial rays (`5°` spacing) across a narrow annulus `[r0 - max(4 px, 0.12 r0), r0 + max(4 px, 0.12 r0)]`
   at `0.5 px` radial steps using bilinear interpolation, excluding rays that approach within `2 px` of the frame
   border.
3. Along each valid ray, locate the strongest radial intensity gradient peak (`|dI/dr| >= 5.0` luma/px, softly
   weighted by proximity to `r0` so outer-rim edges beat distant background edges) and refine the peak location to
   sub-pixel precision via 3-point parabolic interpolation.
4. Select inliers with seeded RANSAC (`geometry.ransac_inliers`, seed `0`) followed by 3-iteration Huber-weighted
   algebraic circle fitting (`delta = 1.0 px`).
5. **Fail-closed fallback & raw preservation:** if fewer than 12 inliers remain, `arc_coverage < 0.45`,
   `fit_rms_px > 2.5 px`, or the centre/radius shifts outside the search annulus, `refine_circle` returns the
   raw prior unchanged (`used_refined = False`) with an explicit reason. `refine_candidate_row` always records
   `raw_circle` alongside the refined geometry so raw observations are never overwritten.

## Results

### 1. Synthetic sub-pixel verification (`test_rim_refine.py`)

On synthetic anti-aliased plates (`r = 82.8–84.4 px`) initialized with `±2.0–3.2 px` centre bias and
`±3.8–4.6 px` (`~5%`) radius bias — including a bright inner metallic hub ring (`0.35 r`) and a `60°`
occluding bar/hand wedge — `rim-subpixel-v1` recovers both centre and radius within **`< 0.20 px`** (`< 0.25%`
of radius), and falls back explicitly (`used_refined = False`, `reason = "insufficient_gradient_peaks"`) on
low-contrast frames.

### 2. Frame-0 bounding-box priors vs SAM 3 instance masks (19 on-target frame-0 seeds)

On the 19 development clips where the frame-0 seed is on-target and frame 0 carries a human truth label,
we compare three ways of deriving the seed circle from the selected SAM 3 proposal:

| Frame-0 circle source (19 clips) | Median `|r - r_true| / r_true` | p90 `|r - r_true| / r_true` |
| --- | ---: | ---: |
| `geometry.circle_from_box(box_xyxy)` (box only) | **1.26%** (`0.0126`) | **6.23%** (`0.0623`) |
| `circle_from_box` + **`rim-subpixel-v1`** (box + image rim fit, no mask) | **0.85%** (`0.0085`) | **5.92%** (`0.0592`) |
| `geometry.circle_from_mask` (full 850M SAM 3 mask) | **0.85%** (`0.0085`) | **2.29%** (`0.0229`) |

Refining a bounding-box prior on the stationary frame-0 image reduces median relative radius error by **33%**
(`1.26% -> 0.85%`), matching the median radius accuracy of SAM 3's full instance mask (`0.85%`) without
requiring a segmentation head. This directly supports the Phase B box-detector student (`D-FINE-S`, #248).

### 3. All tracked labelled frames (`run-2` SAM 2.1 and `run-edgetam-1` EdgeTAM)

Across all matched labelled frames (`316` frames on `track_sam2_sam3seed`, `293` frames on
`track_edgetam_sam3seed`), initializing `rim-subpixel-v1` from the already-fitted `mask_contour` circle:

| Run / method | Matched frames | `used_refined` | Raw median `|dr|/r` | Refined median `|dr|/r` | Raw p90 `|dr|/r` | Refined p90 `|dr|/r` | Raw median centre err | Refined median centre err |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `run-2` (`track_sam2_sam3seed`) | 316 | 250 | 1.35% | 1.41% | 4.50% | 4.45% | 2.24% | 2.49% |
| `run-edgetam-1` (`track_edgetam_sam3seed`) | 293 | 228 | 1.30% | 1.43% | 3.96% | 4.49% | 2.19% | 2.43% |

Clips where `rim-subpixel-v1` substantially tightened `mask_contour` radius error include:

- `self-snatch-side-002` (23 frames): median radius error **`1.65% -> 0.65%`** (`p90: 2.76% -> 2.40%`);
- `self-snatch-side-0213-25` (22 frames): median radius error **`1.26% -> 0.60%`**;
- `self-clean-jerk-side-002` (21 frames): median radius error **`0.82% -> 0.71%`**;
- `self-hang-snatch-side-002` (1 frame): radius error **`12.75% -> 7.90%`** (centre error `14.01% -> 8.49%`);
- `vbt-ad613d6b13787901` (1 frame, VBT seed): radius error **`0.23% -> 0.15%`**.

Conversely, on oblique or heavily motion-blurred pull frames (`self-snatch-side-1015-03`: `1.66% -> 1.80%`),
bumper plates exhibit an inner moulded bevel ring (`~0.92 r`) and vertical motion blur along the top/bottom
arcs that compete with the outer rim when `circle_from_mask` is already within `1.3%` of the label.

## What this suggests

1. **Use `rim-subpixel-v1` on frame-0 bounding-box seeds (`fit == "box"`):** For bounding-box detectors
   (Phase B `D-FINE-S` student or GroundingDINO), `rim-subpixel-v1` cuts frame-0 median radius error from
   `1.26%` to `0.85%` (matching SAM 3's segmentation mask median) and provides a rim-fitted circle for
   initializing the synthetic disc mask.
2. **Do not overwrite `mask_contour` on mid-lift pull frames:** Once SAM 2.1 or EdgeTAM is tracking a mask,
   `geometry.circle_from_mask` (`1.30–1.35%` median error) is already as accurate across motion-blurred frames
   as single-frame isotropic radial gradient peak fitting. Achieving `<= 0.5%` scale across an entire lift
   without a calibration stick (#87, #88) requires either an oblique-view **ellipse fit** (`(a, b, theta)` with
   the major semi-axis `a` giving the un-foreshortened plate radius) or locking calibration scale on the
   stationary pre-lift frames rather than re-fitting radius during fast pulls.

## Reproduction

```bash
python -m unittest discover -v -s research/plate-detection/tests -p 'test_rim_refine.py'
research/plate-detection/.venv/Scripts/python.exe research/plate-detection/rim_refine.py \
    --run-dir validation/private/plate-detection-sam3-seed/run-2 --repo-root . \
    --output validation/private/plate-detection-sam3-seed/rim-refine-run-2.json
research/plate-detection/.venv/Scripts/python.exe research/plate-detection/rim_refine.py \
    --run-dir validation/private/plate-detection-sam3-seed/run-edgetam-1 --repo-root . \
    --output validation/private/plate-detection-sam3-seed/rim-refine-run-edgetam-1.json
```

Private evaluation records (SHA-256):

| File | SHA-256 |
| --- | --- |
| `rim-refine-run-2.json` | `de77faed2813d06b6569a2b8e3ff50f6d68d979099c66ef01425b5abfb21466b` |
| `rim-refine-run-edgetam-1.json` | `f5129ed1baef67691543884b37507e04bb5606040e32ea517fea9fdee82d7e7b` |
