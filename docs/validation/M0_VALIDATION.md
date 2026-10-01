# M0 validation protocol

## Purpose

Validation is part of the product, not a post-hoc marketing exercise.

M0 should quantify both tracker accuracy and the error introduced into derived kinematics.

## Initial engineering gates

These are provisional engineering targets, not scientific claims:

| Metric | Initial target |
| --- | ---: |
| Plate-centre tracking MAE | < 3 px |
| Tracking availability in supported clips | > 99% |
| Range-of-motion MAE | < 0.01 m |
| Mean velocity MAE | < 0.05 m/s |
| Peak velocity MAE | < 0.10 m/s |
| Repeat-analysis determinism | 100% |
| Offline processing | faster than video duration on reference hardware |

Targets should be revised after the first benchmark exposes realistic error distributions.

## Dataset dimensions

The fixture set should vary:

- lift type;
- 30/60/120+ fps where available;
- plate colour/texture;
- bright/dark backgrounds;
- camera distance;
- partial occlusion;
- motion blur;
- camera movement;
- portrait/landscape where support is intended.

M0 may define a narrower supported recording envelope. Unsupported conditions should be reported, not hidden.

## Ground truth

Use more than visual inspection.

Possible references, in increasing strength depending on availability:

1. manually digitised centre coordinates;
2. high-frame-rate manual/reference video;
3. encoder/linear-position transducer;
4. validated commercial VBT device;
5. controlled geometric motion rig.

For velocity-device comparisons, document exactly what each device reports (mean concentric, peak, smoothing, phase boundaries, etc.) before comparing values.

## Metrics

Track at minimum:

- pixel centre MAE/RMSE;
- lost-frame percentage;
- maximum consecutive loss;
- calibrated position/ROM error;
- mean/peak velocity error;
- bias and spread across clips.

For later formal validation consider Bland–Altman analysis, ICC where appropriate, and confidence intervals. Correlation alone is not sufficient evidence of agreement.

## Filtering experiment

Every candidate filter runs behind the same Rust contract and benchmark semantics.

Initial candidates:

- raw identity baseline;
- centred moving average;
- timestamp-aware Savitzky–Golay local polynomial fit;
- causal constant-velocity Kalman/state-space baseline.

Record:

- implementation/version and effective typed parameters;
- causal/non-causal latency semantics;
- irregular-timestamp behaviour;
- explicit gap/reset behaviour;
- edge behaviour;
- position MAE/RMSE/bias;
- downstream velocity MAE/RMSE with an explicit maximum continuity gap;
- peak attenuation only when the reference scenario contains an intentionally defined peak;
- peak timing shift only for those peak-bearing scenarios;
- filter segment count and maximum observed input gap;
- runtime and condition/failure notes.

Parameter development must remain separate from held-out validation as far as the available M0
material allows. The committed `filter-experiment` command uses deterministic seeded Gaussian-noise
development signals, averaged across three independent seeds per signal, to select one configuration
per family. It evaluates those frozen configurations on disjoint held-out synthetic signals with
separate seeds. Peak metrics are left null for scenarios that do not define a meaningful peak.

This is contract/regression evidence, not sufficient evidence for a production default. Production
selection remains deferred until the same configurations have real decoded-video/reference evidence
across the supported recording envelope.

See [`FILTER_EXPERIMENTS.md`](FILTER_EXPERIMENTS.md).

## Failure policy

- Never silently fill long tracking gaps.
- Mark unsupported/low-confidence spans.
- Preserve raw observations.
- Make any short-gap interpolation algorithm and threshold explicit and versioned if introduced.
