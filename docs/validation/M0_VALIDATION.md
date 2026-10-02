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

### M0 go/no-go status

ADR-0008 records the 2026-10-02 review outcome as **NO-GO / REWORK**.

At the evaluated M0 evidence state:

| Metric | Review status |
| --- | --- |
| Plate-centre tracking MAE | NOT MEASURABLE YET |
| Tracking availability in supported clips | NOT MEASURABLE YET |
| Range-of-motion MAE | NOT MEASURABLE YET |
| Mean velocity MAE | NOT MEASURABLE YET |
| Peak velocity MAE | NOT MEASURABLE YET |
| Repeat-analysis determinism | PASS |
| Offline processing | NOT MEASURABLE YET |

The statuses are evidence limitations, not implicit target revisions. M1 remains locked until the
re-entry conditions in ADR-0008 are satisfied.

Rework tracking:

- #57 — held-out real-video tracker/filter selection and tracking gates;
- #53 — evidence-backed recording-envelope boundaries;
- #58 — independent physical ROM/velocity validation;
- #59 — phone-class runtime gate.

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
- centred moving average baseline;
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

## Kinematic metric contract

Issue #11 defines the canonical M0 kinematic semantics in
[`KINEMATIC_METRICS.md`](KINEMATIC_METRICS.md).

Velocity uses the versioned `backward-difference@1` method with actual timestamps, an explicit
maximum continuity gap, and an explicit minimum confidence threshold. Mean and peak axis velocity
require exact, explicit timestamp intervals; phase-specific labels such as mean concentric velocity
remain unavailable until phase boundaries are separately defined and validated.

Synthetic analytic tests verify implementation semantics. Real/reference evidence is still required
before the provisional ROM/mean/peak engineering gates can be treated as satisfied for the supported
recording envelope.

### Independent physical/reference study (#58)

[`M0_REFERENCE_STUDY.md`](M0_REFERENCE_STUDY.md) defines the versioned input/result contracts and
headless `kinematic-reference` evaluator for the three unresolved kinematic gates. It requires exact
timestamp alignment, pinned tracker/filter/kinematics provenance, explicit reference uncertainty,
and preserves failed/unsupported cases instead of silently averaging them away.

The tooling does not change the current review status by itself. Until a qualifying independent
physical/reference dataset is captured or imported and evaluated, ROM/mean/peak velocity remain
**NOT MEASURABLE YET** in the M0 evidence report.

## Failure policy

- Never silently fill long tracking gaps.
- Mark unsupported/low-confidence spans.
- Preserve raw observations.
- Make any short-gap interpolation algorithm and threshold explicit and versioned if introduced.
