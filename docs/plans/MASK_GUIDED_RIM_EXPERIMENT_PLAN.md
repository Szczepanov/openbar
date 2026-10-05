# Mask-guided rim sampling: bounded development experiment

Date: 2026-10-05. Owner authorized continuation after the
[radial failure analysis](../analysis/M0_RADIAL_GEOMETRY_FAILURE_ANALYSIS.md).
Configuration declared before producing or scoring the new variant.

## Context, goal and boundaries

The broad strongest-gradient sampler loses consistent rim support on the existing squat, clean
and snatch development clips. Test whether the existing SAM 2.1 small mask can constrain edge
selection without weakening the current geometry gates. No new detector, checkpoint, dependency,
filter, fusion, automatic seed, production integration or held-out evaluation is included.

Implement experiment, evaluate evidence and promote to production are separate decisions. This
work covers the first two. Keep raw inputs and predictions, authoritative timestamps, display
pixel-centre coordinates, strict finite-value validation and coordinate-free loss.

## Fixed configuration

- Inputs: `self-back-squat-side-002`, `self-clean-jerk-side-002`, `self-snatch-side-002` only.
- Coarse centres: the retained historical SAM small-circle predictions, read-only.
- Boundary producer: existing `track_gpu.sam2_masks("small", ...)`, verified existing checkpoint,
  seed-to-end JPEG-q2 cache, same owner-confirmed seed, no refinement feedback.
- Validate masks with the existing seed-area ratio bounds (0.5..1.5). Use the largest connected
  foreground component, matching the existing mask-circle boundary convention.
- Boundary band: **4.0 px**, fixed across all clips. At each pixel centre, compute exact Euclidean
  distance to the nearest opposite-class pixel centre. Bilinearly sample that distance on the
  original radial grid; only gradient samples at distance <=4.0 px are eligible. Eligibility is
  checked before the unchanged subpixel peak refinement.
- Choose the strongest eligible gradient; retain the original contrast test and all other radial
  defaults, including 72 rays, 0.5 px sampling, 60% support and 65% angular coverage. If no eligible
  sample exists, that ray supplies no edge. No mask-centre fallback or inferred edge is allowed.
- Keep the 30%-of-seed-radius radial search band and fixed seed radius. This isolates boundary
  conditioning; it does not test changing apparent scale, ellipse fitting or threshold relaxation.

The model-derived boundary is a hypothesis, not independent rim or physical ground truth. GPU and
image-measurement environments must be recorded separately; the available GPU environment differs
from the pinned OpenCV 4.12.0 / NumPy 2.2.6 measurement environment. Do not install packages or
claim the new masks are byte-identical to a historical model run.

## Ordered work and files

1. Retain this plan's hash before evaluation. Export verified masks with existing GPU helpers to
   this worktree's `validation/private/mask-guided-rim/`, with input/model/source/environment hashes.
2. Prototype the single sampling change locally by reusing `vision.radial_center`'s source body
   and replacing only candidate selection; retain the resulting source and its hash. Original
   estimator and public runner remain unchanged unless later evidence justifies a reusable tool.
3. Leave a runnable synthetic check: a circular rim with a stronger competing outer ring, unchanged
   results when the band admits everything, and no coordinate on absent or weak edge evidence.
4. Decode source-resolution BGR with the existing validated decoder; emit a separate strict
   `tracker-prediction-v1` stream and diagnostics. Keep the manual seed distinct from measured rim.
5. Compare with default radial/SAM ROIs and historical SAM small-circle on exact labelled times.
   Reuse `motion_metrics.evaluate`, `paired` and existing false-track diagnostics. Retain failures,
   seed-excluded centre/displacement errors, all-frame and labelled coverage and common support.
6. Repeat CPU measurement from the same frozen masks; compare prediction and sidecar bytes. Record
   GPU producer repeatability separately from this frozen-input check. Write an aggregate result
   under `docs/analysis/` and link it from the failure analysis.

## Verification, decision and stop

Run the prototype's synthetic check, prediction schema checks, the 79-test bar-path suite and
`git diff --check`. No Rust code or canonical fixture semantics change; Rust integration gates
become applicable if any such boundary is touched.

Reject the variant if squat/clean coverage does not improve, surviving error increases, or silent
false-track behaviour worsens. Do not infer improvement from incomparable surviving subsets;
report paired centre/delta evidence and its size. Zero available displacement intervals are
unsupported, not a zero error. Any encouraging result still requires independent annotation
repeatability, a complete viable pipeline and the existing #57 freeze/held-out process.

Stop after this one configuration. On failure, retain the negative result and prioritize dense
development labels/controlled capture before adding complexity. Public changes are research docs;
private masks, frames, coordinates, local prototype and checkpoints stay out of Git. No schema,
golden fixture or ADR changes are intended.
