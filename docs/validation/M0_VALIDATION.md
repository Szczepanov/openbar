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

Every candidate filter should run against the same fixture/reference set.

Record:

- parameters;
- latency/edge behaviour;
- position error;
- velocity error;
- peak attenuation;
- failure modes.

Select the production default from measured trade-offs, not visual smoothness.

## Failure policy

- Never silently fill long tracking gaps.
- Mark unsupported/low-confidence spans.
- Preserve raw observations.
- Make any short-gap interpolation algorithm and threshold explicit and versioned if introduced.
