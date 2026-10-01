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
   - non-causal;
   - asymmetric shifted windows at clip/segment boundaries;
   - a segment shorter than `polynomial_order + 1` passes through unchanged rather than
     fabricating support.
4. `constant-velocity-kalman@1`
   - causal two-state position/velocity model per axis;
   - each transition uses the measured timestamp delta;
   - process noise, measurement noise, and initial velocity variance are explicit;
   - state resets after a gap larger than `max_gap_s`;
   - no prediction is emitted for missing timestamps.

## Benchmark metrics

`openbar_core::benchmark::evaluate_filter_case` requires the filtered trajectory to retain the
reference sample count and timestamps. It reports:

- position MAE/RMSE and maximum radial position error;
- X/Y position bias;
- downstream velocity MAE/RMSE over contiguous intervals only;
- reference and filtered peak speed over contiguous intervals only;
- peak attenuation and attenuation fraction;
- peak timing shift.

The CLI additionally records:

- edge position MAE over the first/last samples;
- input/output sample counts;
- maximum observed timestamp gap;
- wall-clock runtime as environment-sensitive diagnostic data.

A filter that changes timestamps or synthesizes samples is rejected by the filter benchmark
contract rather than receiving deceptively favourable metrics. The benchmark also receives an
explicit `max_velocity_gap_s` continuity threshold. Finite differences that would bridge a larger
missing/lost interval are excluded from velocity and peak metrics rather than treating the span as
supported motion.

## Development versus held-out validation

Run:

```bash
cargo run -p openbar-cli -- filter-experiment \
  --output target/filter-experiment.json
```

The command uses deterministic synthetic signals in two disjoint groups.

Development signals cover:

- constant position + noise;
- constant velocity + noise;
- a smooth curved trajectory;
- irregular/VFR-style timestamp spacing.

Within each filter family, candidate parameters are selected by mean development velocity RMSE,
with mean position RMSE as a deterministic tie-break. The rule selects one configuration per family;
it does not select a production winner.

Held-out synthetic signals then cover:

- a sharp velocity feature for peak attenuation/phase shift;
- a short clip for boundary behaviour;
- a short missing span;
- a long tracking-loss span that exceeds the configured continuity threshold.

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
