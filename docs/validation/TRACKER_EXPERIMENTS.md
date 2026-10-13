# M0 first tracker experiments

## Purpose

Issue #7 introduces two manual-seed tracking candidates under one frame contract and the existing
benchmark evaluator. This is an engineering comparison surface, not a claim that either tracker is
production-ready or scientifically validated.

Measurement rules remain unchanged:

- timestamps are authoritative;
- the manual seed is immutable input;
- lost tracking is explicit;
- no last-known coordinate is emitted as if measured;
- no interpolation is performed;
- target bounds exist only for tracked/low-confidence samples; v1 bounds retain the manual-seed target size and are not a scale estimate;
- visibility is explicit but v1 reports `unknown` rather than inferring occlusion from tracker
  failure;
- confidence values are algorithm-specific signals, not interchangeable probabilities.

## Frame/decode boundary

`openbar-tracking` consumes display-oriented 8-bit grayscale images, timestamps, optional frame
indices, the canonical `ManualTargetSeed`, and explicit configuration. It does not decode video
or depend on containers, codecs, cameras, Flutter, or platform media APIs.

ADR-0005 records this separation. It lets a future native/video layer provide decoded image views
without moving measurement logic into the UI or making nominal FPS authoritative.

All tracker observations, seeds, annotations and bounds are in decoded display-oriented pixel
coordinates after rotation, per ADR-0007. A tracker that runs on a transformed frame must
map its output back before emitting observations.


## Common output

Every `TrackerRun` records tracker ID, implementation, version, effective configuration,
confidence semantics, timestamped tracked/lost observations, optional raw centre/bounds, explicit
loss reason, quality score, displacement from the last measured centre, and reacquisition after
loss.

`TrackerRun::benchmark_predictions()` adapts directly to
`openbar_core::benchmark::evaluate_tracker_case`, so MAE/RMSE, availability, loss spans and
timestamp alignment remain defined in one place.

## Candidate A — fixed template

ID: `template-sad-v1`.

The square seed-frame patch remains fixed. Each later frame searches around the last measured
centre and minimizes normalized mean absolute pixel difference (NMAD).

```text
confidence = 1 - NMAD
```

Likely strengths: deterministic, easy to inspect, no explicit plate-colour assumption, useful
correlation-family baseline.

Expected failures: seed-to-current appearance change, scale/rotation/perspective change, large
inter-frame motion, occlusion, look-alike background regions, and drift in realistic clutter.

The template intentionally does not update in v1; silently updating the reference would make drift
harder to interpret.

## Candidate B — local contrast centroid

ID: `local-contrast-centroid-v1`.

The seed estimates target-vs-local-ring intensity contrast and polarity. Later frames calculate a
contrast-weighted centroid in a local search window.

```text
confidence = current contrast mass / seed contrast mass, clamped to [0, 1]
```

Likely strengths: materially different from template matching, less dependent on exact internal
texture, tolerant of some simple size/visibility change.

Expected failures: weak/reversed contrast, same-polarity distractors, centroid bias during
asymmetric occlusion, clutter, and motion beyond the local search region.

## Deterministic comparison runner

Run:

```bash
cargo run -p openbar-cli -- tracker-experiment \
  --output target/tracker-experiment.json
```

Both trackers see identical procedural frames/seeds and their predictions go through the common
benchmark evaluator. Scenarios cover stationary motion, slow/fast translation proxies, full
occlusion + re-entry, lower contrast, partial occlusion, and a smaller-target distance/scale proxy.

The versioned JSON artifact includes normal tracker metrics plus loss-reason counts, mean reported
confidence, count of errors above 2 px while confidence is at least 0.8, runtime, selected media
duration, and media-seconds per wall-second. Runtime is environment-sensitive and is not part of
deterministic accuracy semantics.

The comparison uses benchmark `min_confidence = 0`: each tracker makes its own explicit
tracked/lost decision, and the two confidence signals have different definitions. Cross-algorithm
confidence thresholds would require separate calibration evidence.

## Canonical fixture integration

Tests also load the committed manual seed and annotation document for
`synthetic-clean-side-12`, generate matching procedural imagery, and pass both implementations
through the same timestamp-based evaluator. This checks compatibility with the #3/#4/#5/#6
contracts without pretending synthetic pixels are real-video evidence.

## Real decoded video: `tracker-run`

Issue #40 adds the ADR-0006 frame source and a `tracker-run` subcommand that feeds real
decoded frames to both baselines:

```bash
cargo run --locked -p openbar-cli -- tracker-run \
  --manifest validation/fixtures/public/manifest.json --fixture synthetic-clean-side-12 \
  --seed validation/fixtures/public/seeds/synthetic-clean-side-12.manual-target-seed-v1.json \
  --output-dir target/tracker-run-smoke
```

- **Media and checks.** Media comes from the manifest's `media.repository_path`, resolved from
  the current directory, or from `--media`. Its SHA-256, encoded size and rotation must match
  the manifest, and the seed is validated against the decoded display frame.
- **Range and memory.** `--start-s/--end-s` select an inclusive window. Its boundaries are
  exact, with no tolerance. `--max-frame-memory-mib` (default 2048) caps the frames held in
  memory.
- **Seed check.** The seed must lie within 0.0005 s of a selected decoded frame. Otherwise the
  run stops before tracking and reports the gap to the nearest frame.
- **Configuration.** Both trackers use their default configuration, with two changes. Seed
  timestamp tolerance is set to 0.0005 s, the annotation decoder-match tolerance.
  `--search-radius-px` overrides the search radius for both trackers when given.
- **Output.** One tracker-prediction-v1 document is written per tracker, named
  `<fixture>.<tracker>.prediction-v1.json`. `implementation.config` records the tracker
  config, the seed and the full frame-source provenance. The benchmark harness consumes these
  documents unchanged.
- **Coverage.** Trackers observe from the seed frame onward; frames before the seed are decoded
  but not tracked.
- **Failures.** A tracker that rejects its input fails without blocking the other tracker. The
  command still exits non-zero.

### First real-footage observations (private, unannotated)

These are qualitative observations from one private hang-snatch clip (720×1280 display, VFR,
30 fps nominal, 90° rotation). The seed was estimated from intensity profiles (about ±5 px).
They are not accuracy evidence. That needs annotations and the benchmark harness.

| Run | Outcome |
| --- | --- |
| `template-sad-v1`, default search radius 12 px | Followed the plate through the dip, lost it in the explosive pull (where the plate moves about 30+ px/frame), then followed the lifter's body for the rest of the clip. Every frame stayed `tracked`, at confidence about 0.85–0.97. **No loss was reported.** Runtime about 0.36× real time. |
| `template-sad-v1`, search radius 40 px | Visually stayed on the plate through pull, catch and overhead, with a visible offset and the plate partly leaving the top of the frame; lagged when the bar was dropped. Runtime about 0.05× real time. |
| `local-contrast-centroid-v1` | Refused the seed: plate/background contrast 11.9 is below its 12.0 minimum (grey bumper plate against grey clothing and wall). |

Implications to test with annotated real clips:

- Template confidence can remain high on a wrong target. Availability and confidence alone
  cannot detect a silent false track, so position error against annotations is required.
- A fixed per-frame search radius has to cover peak bar speed in pixels per frame. That speed
  scales with frame rate and plate size in pixels, so a constant radius is fragile.
- Exhaustive SAD search over a plate-sized template is far slower than real time at realistic
  radii. Meeting the offline-speed gate needs a cheaper search or a different candidate.

## Candidate C — `csrt-gray-v1` (experimental, under construction)

A single-channel, fixed-support, fixed-scale correlation-filter variant in the CSRT family
(Lukezic et al., CVPR 2017), written independently in `openbar-tracking::csrt`. It is **not yet a
tracker**: no `ManualSeedTracker` implementation, CLI exposure or evidence exists, and nothing here
selects it for production. The implementation plan is
[`docs/plans/CSRT_GRAY_TRACKER_IMPLEMENTATION_PLAN.md`](../plans/CSRT_GRAY_TRACKER_IMPLEMENTATION_PLAN.md);
the slice status is recorded there.

| Slice | Content | Status |
|-------|---------|--------|
| S1 | deterministic FFT and transcendental-free math | merged (#171) |
| S2 | patch geometry, feature preprocessing, Gaussian label, disk support | merged (#174) |
| S3 | constrained filter learning and response | implemented, internal only |
| S4–S5 | tracker loop and loss policy, synthetic benchmark | not started |

### S2 contract (internal; no public API)

- **Crop.** Square, side the next power of two of `ceil(4 * radius_px + 1)`, minimum 8, maximum 512
  (a prototype ceiling, not a measured phone budget). Crop `(i, j)` reads image
  `(cx + i - side / 2, cy + j - side / 2)`; the anchor sits at crop index `(side / 2, side / 2)`.
  Tracking is integer-pixel; fractional seed centres use the shared `rounded_point`.
- **Edges.** A crop not fully inside the frame is *unavailable*: no padding, reflection, wrapping
  or clipping. At the seed this is an error; on later frames it will become a loss. This is a
  deliberate, conservative limitation.
- **Features.** Mean-subtracted, scaled to unit population RMS, then multiplied by the separable
  periodic Hann window, with no renormalisation afterwards. Mean and RMS come from exact integer
  sums of the 8-bit intensities, so a flat crop has exactly zero variance. An RMS below one
  intensity level is insufficient contrast and is reported in original intensity units.
- **Label.** Gaussian with `sigma_px` peaking at FFT index `(0, 0)`, using wrapped axis distances
  and the deterministic exponential. Only supported square patch sizes are accepted.
- **Support.** Fixed binary disk of the seed radius centred at `(side / 2, side / 2)`, requiring
  `4 * radius + 1 <= side`; the published method's adaptive spatial reliability is deliberately
  omitted.
- **Failures.** Invalid radius, sigma or patch size, and non-finite results, are typed
  `NumericError`s, never plausible coordinates.

### S3 contract (internal; no public API)

- **Objective and normalization.** Restricted spatial ridge objective: `||A*g - y||^2 + lambda*||g||^2`,
  constrained to $g = m \odot g$ with circular correlation matrix $A$. By Parseval, equals
  `(1/N) * ||conj(H)*X - Y||^2 + lambda*||g||^2`. The unconstrained full-support ridge solution is
  `H = X * conj(Y) / (|X|^2 + lambda)`.
- **Constrained learning via scaled ADMM.** Solved in spatial/spectral alternating steps:
  1. `GminusU = FFT(g - u)`;
  2. `H = (X*conj(Y) + mu*GminusU) / (|X|^2 + mu)`;
  3. `h = real(inverse(H))`;
  4. `g = m * (mu / (lambda + mu)) * (h + u)`;
  5. `u = u + h - g`;
  6. `mu = if mu >= mu_max/mu_growth { mu_max } else { mu * mu_growth }`.
  Default settings: `lambda = 0.01`, 4 fixed iterations, `mu_initial = 1.0`, `mu_growth = 3.0`,
  `mu_max = 81.0`. Safe saturation prevents float overflow during growth updates.
- **Support enforcement.** `train_filter` returns `FFT(g)` (the transform of the masked spatial filter),
  guaranteeing that unconstrained frequency components do not violate the spatial boundary.
- **Response calculation.** `response` computes `inverse(conj(H) * X)` and extracts real values after
  validating that imaginary residuals do not exceed `1e-9 * max(1, max_abs_real)`. Validated against
  direct circular correlation oracle `r[ty, tx] = sum_{py, px} g[py, px] * x[(py+ty)%H, (px+tx)%W]`.
- **Resource boundary & validation.** Transform width and height must each be powers of two in
  1..=512. Vector lengths must match `width * height`. Spectra must be finite and Hermitian within
  `1e-9 * max(1, abs)`. Mask entries must be exactly 0 or 1 with non-empty support.
- **Observed evidence.** Verified against independent direct spatial correlation, closed-form full ridge,
  dense spatial ridge with test-only Gaussian elimination, shift signs (+X right, +Y down), S2/S3
  composition, and bit-level determinism with pinned digest `0x4045_6d96_94a9_7f6a`.

Initial parameters (`sigma_px = 2`, loss and tracking PSR thresholds, learning rate, ADMM settings)
are frozen in the plan for the later slices and have not been evaluated.

## Carry-forward recommendation

Carry both candidates into real decoded-video benchmarking:

- keep `template-sad-v1` as the deterministic correlation-family baseline;
- keep `local-contrast-centroid-v1` as the independent appearance/segmentation baseline;
- do not select a production winner from procedural cases.

Parameters are intentionally fixed across the procedural scenario set rather than tuned per case.
Real fixtures must test blur, compression, camera movement, distance, perspective, realistic gym
clutter, plate/background variation and lift-specific conditions. If both baselines fail the same
conditions, add a feature/optical-flow candidate or justified hybrid/reinitialization strategy
under the same contract instead of overfitting one baseline.

## Dependencies and clean-room compliance

No external CV dependency, model, dataset, competitor code, or proprietary implementation detail
is used. Production tracker code is project-owned Rust over generic image-processing concepts and
depends only on `openbar-core`.
