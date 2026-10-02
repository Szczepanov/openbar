# Open-source VBT and CV reference review

## Purpose

This note records engineering lessons from public repositories reviewed while developing OpenBar.
It is a research/provenance document, not authorization to copy third-party implementation code,
model weights, datasets, or assets.

The review is intentionally separated from OpenBar's normative architecture and measurement
contracts. External projects may suggest experiments or product requirements; OpenBar still selects
methods only from its own evidence and implements authoritative measurement logic independently.

## Sources reviewed

| Source | Observed role | Licence status at review | OpenBar use |
| --- | --- | --- | --- |
| `kostecky/VBT-Barbell-Tracker` | OpenCV proof-of-concept for optical barbell tracking and live VBT feedback | Repository declares CC0-1.0 | Research reference; simple controlled-marker baseline ideas |
| `hybridgroup/gocv` | Go bindings and C-style wrapper around OpenCV | Apache-2.0 | Integration/reference architecture only; no Go dependency proposed |
| `LeFaillerFrancois/app_vbt` | Desktop DeepLabCut VBT application with calibration, filtering, rep editing and load-velocity analysis | No repository licence file found during review | Behaviour/research reference only; do not copy code, model weights, datasets or assets |
| `LeFaillerFrancois/BarTracker_app_vbt_validation` | Companion agreement study against Metric VBT and Qwik VBT | No repository licence file found during review | Methodological reference only; do not import code/data without explicit permission/licence |
| `twistedfall/opencv-rust` | Rust bindings for OpenCV | MIT for the binding project | Candidate adapter to evaluate only if a future OpenCV dependency is justified |

A public repository without an explicit licence remains publicly readable but is not thereby granted
for reuse or redistribution. Before any third-party code/data/model material enters OpenBar, record
the exact source, version/commit, licence, redistribution rights, commercial implications and any
transitive native/system dependencies.

## Lessons from VBT-Barbell-Tracker

### Controlled marker as a benchmark/reference mode

The project reports moving from generalized CNN/object-tracker experiments to a deliberately
distinctive coloured circular target because the simpler approach was faster and more reliable in
its intended controlled environment.

OpenBar should treat that as a useful validation idea, not as the default athlete workflow. A
controlled chroma/fiducial target can provide an inexpensive secondary reference trajectory when
recording benchmark clips:

```text
manual digitisation
        |
production-candidate tracker
        |
controlled marker tracker
        |
compare all against the same timestamps/fixtures
```

Any such tracker remains a separately versioned experimental implementation with explicit supported
conditions.

### Intrinsic camera calibration

The project performs chessboard-based camera calibration and frame undistortion before measurement.
OpenBar M0 currently documents lens distortion as a limitation rather than correcting it. That
motivates a controlled experiment comparing the same fixtures with and without intrinsic-distortion
correction. Promotion requires measured improvement in position/ROM/velocity error large enough to
justify added capture/runtime complexity.

### Nominal-FPS anti-pattern

The POC reads video FPS and then overrides it with a fixed value before deriving velocity. OpenBar
must preserve its existing invariant: authoritative timestamps, never assumed constant nominal FPS,
drive derivatives.

## Lessons from GoCV

GoCV itself is not a recommended OpenBar dependency. Adding Go/cgo between the Rust core and OpenCV
would add another runtime/build boundary without solving a problem that cannot be tested directly
from Rust.

Useful engineering lessons are:

1. **Narrow native adapter boundary.** GoCV keeps OpenCV C++ behind a wrapper boundary. If OpenBar
   adopts OpenCV later, OpenCV-specific frame/matrix types should remain inside an adapter crate or
   module rather than leak into `openbar-core`.
2. **Native-resource lifecycle testing.** GoCV explicitly profiles matrix allocations because native
   buffers are not ordinary managed-language objects. A future Rust/OpenCV/GPU path should include
   stress/leak tests for frame/matrix/buffer ownership.
3. **Calibration tooling.** OpenCV's calibration and ArUco/ChArUco capabilities make it a useful
   research tool for quantifying camera-intrinsic/lens-distortion effects, even if OpenCV never
   becomes part of the production measurement path.
4. **Dependency cost is part of the decision.** Native OpenCV brings packaging, linking, platform and
   binary-size implications. Accuracy/runtime benefit must justify that cost.

## Lessons from app_vbt

### Multi-frame visible plate-size estimation

The project tracks plate top/centre/bottom and uses a median visible plate diameter to calculate
physical scale. OpenBar's current M0 method deliberately anchors calibration to one explicit,
human-confirmed seed observation. The external approach motivates a benchmark, not a replacement.

Compare:

- single manual reference diameter;
- median diameter over high-confidence frames;
- robust confidence-weighted multi-frame estimates.

Also evaluate visible-diameter variation as a possible geometry-quality diagnostic. Material
variation may indicate perspective/depth change, camera motion, plate foreshortening or tracker
error. It must not silently mutate the metres-per-pixel scale unless a separately versioned
calibration method is validated and adopted.

### Butterworth low-pass filtering

The project uses a fourth-order zero-lag Butterworth low-pass filter (documented with an 8 Hz
cutoff) around its kinematic processing. This is a credible additional research family, but not a
drop-in OpenBar filter.

Classic digital Butterworth designs normally assume a regular sample interval. OpenBar's persisted
timestamps are authoritative and may be irregular. A Butterworth experiment therefore needs an
explicit timestamp-regularity policy:

- define and record the regularity/jitter criterion;
- never substitute nominal FPS for measured timestamps;
- do not silently resample VFR input merely to make the filter applicable;
- fail/mark unsupported when assumptions are violated, or evaluate a separately versioned
  timestamp-aware/resampling method with explicit interpolation provenance;
- benchmark cutoff/order selection on development data only, then freeze before held-out
  evaluation;
- compare position error, downstream velocity error, peak attenuation, phase shift, edge behaviour,
  gap behaviour and runtime against existing candidates.

Until that experiment exists, the four current M0 filter families remain the normative candidates.

### Rep detection plus manual correction

The project combines automatic rep/phase estimates with an editor. This is a strong future product
interaction pattern. OpenBar should retain the automatic estimate and persist manual boundary
changes as explicit provenance rather than silently replacing algorithm output.

This remains post-M0 because rep/event interpretation is downstream of validated trajectory and
kinematics.

### Time-normalized rep trajectories

The project normalizes rep trajectories to a fixed number of phase samples to compare mean paths and
variation across repetitions. This is useful for future lift-to-lift/set-to-set comparison, but
normalization must remain a derived comparison representation. It must not replace timestamped raw
or calibrated trajectories.

### Load-velocity profiling

Multi-load load-velocity regression is a useful later athlete-analysis feature. It depends on
validated rep boundaries, construct-matched mean velocity and trustworthy load metadata, so it does
not belong on the M0 measurement critical path.

### Anti-pattern: last-known-position substitution

The webcam code can reuse the last reliable point when model confidence falls. That can create a
visually stable preview while fabricating a stationary measurement. OpenBar must continue to encode
lost/low-confidence observations explicitly and keep any purely visual preview interpolation outside
authoritative measurement data.

### Anti-pattern: non-uniform resize without source-space recovery

The reviewed video path resizes to fixed width/height dimensions. A non-uniform resize can turn a
circle into an ellipse and create different X/Y spatial scales.

OpenBar should preserve this invariant for every future preprocess/inference path:

> any crop, rotate, resize, letterbox or stabilization transform that changes image coordinates must
> be explicitly recorded and invertible back to the canonical display/source coordinate space used
> by measurement, or must use a geometry-preserving transform whose effect is fully specified.

A model may infer on a resized/cropped tensor, but measurements must be expressed in the canonical
coordinate system before calibration.

## Lessons from BarTracker_app_vbt_validation

The companion study usefully reports regression/correlation together with Bland-Altman bias and
95% limits of agreement. That supports OpenBar's existing rule that correlation alone is not
agreement.

Its limitations also identify failure modes OpenBar should avoid in formal validation:

- a small, single-athlete, single-exercise, single-camera dataset cannot establish general product
  validity;
- commercial applications are useful comparators only when their construct/phase/filter semantics
  are definition-matched; they are not automatically ground truth;
- repetitions must not be paired merely by array position or by truncating both outputs to the
  shorter length, because one missed/extra rep can shift every subsequent pair;
- repeated reps from the same athlete/session are clustered observations and should not be treated
  as independent when computing inferential uncertainty.

OpenBar's independent-reference study should therefore use explicit rep/interval identity or exact
timestamp alignment, report misses/extra detections separately, retain per-condition results, and
add confidence intervals/cluster-aware uncertainty when the sample structure justifies inferential
statistics.

## Experiments justified by this review

These are proposed research tasks, not current M0 requirements or production defaults.

### E1 — Controlled marker reference tracker

Question: does a deliberately distinctive circular/chroma/fiducial target provide a sufficiently
accurate secondary reference to reduce manual annotation burden on controlled fixtures?

Required evidence:

- compare against manual digitisation;
- quantify centre error and failure rate;
- include blur/occlusion/lighting/scale conditions;
- keep the marker tracker out of production selection unless explicitly reviewed.

### E2 — Lens-distortion sensitivity

Question: how much do phone-camera intrinsics/lens distortion affect OpenBar metrics inside and
outside the intended recording envelope?

Compare identical measurement runs before/after calibrated undistortion and stratify error by field
position. Do not require athlete calibration workflows unless evidence shows meaningful product
benefit.

### E3 — Multi-frame calibration robustness

Question: does robust multi-frame visible-diameter estimation reduce physical-position/ROM error
relative to the explicit single-seed diameter without introducing circular dependence on tracker
quality?

Preserve the existing single-reference method as the control.

### E4 — Butterworth research family

Question: under sufficiently regular timestamps, does a frozen zero-phase Butterworth family improve
held-out kinematic accuracy/peak preservation relative to the current filter candidates?

Do not assume 8 Hz or any order is optimal for OpenBar. Tune on development evidence, freeze, then
evaluate held-out real/reference data. Irregular timestamp handling must be explicit.

### E5 — Geometry-transform invariant tests

Question: can every future preprocessing path prove that output coordinates map correctly back to
canonical measurement coordinates?

Add synthetic checks for uniform resize, non-uniform resize, crop, rotation and letterbox transforms
before such preprocessing enters production inference.

## Scope decision

This review does **not** add Go, GoCV, DeepLabCut, OpenCV, model weights, new production filters,
automatic rep detection, pose estimation or live camera processing to M0.

The immediate value is to improve the experiment backlog and validation contracts while preserving
the current Rust-first, timestamp-authoritative, explicit-uncertainty architecture.


## Execution plan

The ordered implementation plan, decision gates, stop conditions and integration points with #57 and
#58 are maintained in
[`docs/plans/VBT_REFERENCE_EXPERIMENTS_IMPLEMENTATION_PLAN.md`](../plans/VBT_REFERENCE_EXPERIMENTS_IMPLEMENTATION_PLAN.md).

The plan is intentionally stricter than this analysis note: it separates implementation from
evaluation and production promotion, and allows the correct outcome of an experiment to be
**reject/defer with no production change**.
