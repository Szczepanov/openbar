# M0 filtering experiments

Issue #10 establishes the common filtering contract and evidence path. The purpose is not to make
trajectory plots look smooth; it is to quantify how candidate filters change measured position and
downstream timestamp-derived velocity.

## Common Rust contract

All candidates are applied through `openbar_core::filtering::apply_filter`.

A run records:

- implementation identity and version;
- effective typed parameters in the canonical `ImplementationProvenance`;
- causal/non-causal behaviour;
- irregular-timestamp semantics;
- missing-span/gap semantics;
- boundary/edge semantics;
- latency semantics.

Every filter preserves the input sample timestamps one-for-one. No candidate synthesizes samples for
missing timestamps, and raw input samples are never overwritten.

The four M0 candidates are:

1. `raw-identity@1`
   - identity control;
   - causal;
   - no smoothing, interpolation, or timestamp shift.
2. `centered-moving-average@1`
   - equal sample weighting inside a centred window;
   - non-causal;
   - truncated windows at segment boundaries;
   - windows cannot cross a gap larger than `max_gap_s`.
3. `timestamp-aware-savitzky-golay@1`
   - local polynomial least-squares fit evaluated at each authoritative timestamp;
   - uses actual timestamp offsets and does not require uniform resampling;
   - normalizes each local timestamp basis to roughly `[-1, 1]` before solving, avoiding false singularity at 120/240+ fps while leaving the fitted value at the target timestamp unchanged;
   - non-causal;
   - asymmetric shifted windows at clip/segment boundaries;
   - a segment shorter than `polynomial_order + 1` passes through unchanged rather than
     fabricating support.
4. `constant-velocity-kalman@1`
   - causal two-state position/velocity model per axis;
   - each transition uses the measured timestamp delta;
   - process noise is recorded as acceleration variance in `m²/s⁴`;
   - measurement noise is recorded as position variance in `m²`;
   - initial velocity variance is recorded in `m²/s²`;
   - `confidence_window_samples` explicitly controls recent-confidence memory; output confidence is the minimum over that finite window and can recover once a low-confidence observation leaves it;
   - state and confidence history reset after a gap larger than `max_gap_s`;
   - no prediction is emitted for missing timestamps.

## Benchmark metrics

`openbar_core::benchmark::evaluate_filter_case` requires the filtered trajectory to retain the
reference sample count and timestamps. It reports:

- position MAE/RMSE and maximum radial position error;
- X/Y position bias;
- downstream pointwise velocity MAE/RMSE over supported derivative segments;
- horizontal and vertical ROM absolute error when the full comparison range is supported;
- mean X/Y velocity absolute error over the exact full comparison interval when it is supported;
- peak signed X/Y velocity absolute error only when the scenario declares peak metrics applicable;
- reference and filtered Euclidean peak speed over supported intervals only when the scenario defines a meaningful peak;
- peak-speed attenuation/attenuation fraction and timing shift only for those peak-bearing scenarios.

The ROM/mean/axis-peak quantities use the canonical issue #11 semantics documented in
[`KINEMATIC_METRICS.md`](KINEMATIC_METRICS.md). Euclidean `peak_speed` remains a filter
diagnostic and must not be relabelled as signed vertical peak velocity.

The CLI additionally records:

- edge position MAE over the first/last samples;
- input/output sample counts;
- filter segment count, making an over-small `max_gap_s` that degenerates into one-sample segments visible;
- maximum observed timestamp gap;
- wall-clock runtime as an environment-sensitive console diagnostic; it is deliberately excluded from the retained JSON evidence artifact so identical inputs/configuration keep deterministic machine-readable output.

A filter that changes timestamps or synthesizes samples is rejected by the filter benchmark
contract rather than receiving deceptively favourable metrics. The benchmark also receives an
explicit `max_velocity_gap_s` continuity threshold. That threshold is passed into the authoritative
`backward-difference@1` kinematics method itself, so a derivative across a larger missing/lost
interval is never created and then merely hidden by reporting code.

## Development versus held-out validation

Run:

```bash
cargo run -p openbar-cli -- filter-experiment \
  --output target/filter-experiment.json
```

The command emits filter evidence schema version `3` / experiment version
`m0-filter-comparison-v3`. It uses deterministic synthetic signals in two disjoint groups. Measurement noise is generated with a small dependency-free seeded SplitMix64 + Box–Muller Gaussian generator. Development metrics are averaged across three independent seeds per signal and X/Y use independent streams. Held-out scenarios use separate seeds.

Development signals cover:

- constant position + noise;
- constant velocity + noise;
- a smooth curved trajectory;
- irregular/VFR-style timestamp spacing.

Within each filter family, candidate parameters are selected by mean development velocity RMSE,
with mean position RMSE as a deterministic tie-break. The rule selects one configuration per family;
it does not select a production winner. The Kalman grid varies both acceleration variance and
measurement variance; it does not receive the synthetic generator's exact variance as a fixed
privileged parameter.

Held-out synthetic signals then cover:

- a sharp velocity feature for peak attenuation/phase shift, where peak metrics are applicable;
- a short clip for boundary behaviour, where peak fields remain null;
- a short missing span, where peak fields remain null;
- a long tracking-loss span that exceeds the configured continuity threshold, where peak fields remain null.

Held-out results are not fed back into tuning.

## Synthetic evidence is not production evidence

The experiment establishes that the contract is deterministic and benchmarkable and gives an early
quantitative comparison under known signals. It does not model all real phone-video errors.

In particular, it does not establish robustness to the full distribution of:

- motion blur;
- compression/codec artefacts;
- partial occlusion;
- camera movement;
- gym clutter and distractors;
- perspective/parallax;
- plate appearance and scale changes;
- tracker-specific correlated error.

For that reason the production filter selection is explicitly **deferred**.

## Required next evidence

Before choosing a production default:

1. run the selected per-family configurations on the same real annotated M0 fixture set;
2. add reference velocity material where the compared construct and phase definition are aligned;
3. stratify results by recording condition;
4. compare position error, downstream velocity error, peak attenuation, phase shift, edge/gap
   behaviour, and runtime;
5. select only when the measured trade-offs justify the choice.

Do not promote the moving-average baseline, Savitzky–Golay, or Kalman filter merely because its
overlay looks smoother.


## Research candidate: zero-phase Butterworth

A reviewed public VBT project uses a fourth-order zero-lag Butterworth low-pass filter with an
8 Hz cutoff. That observation is sufficient to justify a research candidate, but not to adopt its
parameters or implementation.

OpenBar's current four M0 filter families remain unchanged until a separately reviewed experiment is
implemented. Any Butterworth experiment must preserve the existing measurement contract:

- authoritative timestamps remain authoritative;
- raw samples are preserved;
- no missing timestamp is fabricated;
- irregular/VFR input must not be silently treated as uniformly sampled;
- any resampling/interpolation, if ever evaluated, requires a separately versioned method and
  explicit provenance;
- cutoff/order tuning uses development data only;
- the selected configuration is frozen before held-out evaluation.

The experiment should compare at least:

- multiple plausible cutoff frequencies rather than assuming 8 Hz is transferable;
- fourth order versus any additional order only when the grid is pre-declared;
- position MAE/RMSE;
- downstream mean/peak velocity error;
- peak attenuation and timing shift;
- edge/transient behaviour;
- gap/loss behaviour;
- sensitivity to timestamp jitter;
- runtime.

The first implementation should include a fail-closed timestamp-regularity policy. A simple
frequency-domain/digital filter that requires regular spacing must report unsupported input when
measured timestamp jitter exceeds its documented assumption rather than substituting nominal FPS.

Promotion to a production candidate still requires the same held-out real/reference evidence as the
existing families.
