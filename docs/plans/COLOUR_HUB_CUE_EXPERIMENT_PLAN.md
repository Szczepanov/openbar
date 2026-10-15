# Colour and hub cues for rim sampling: bounded development experiment

Date: 2026-10-15. Owner approved this plan and its thresholds in chat before any code was written.
Configuration and decision rules are fixed here, before producing or scoring any variant. The
SHA-256 of this file is recorded outside it (commit message and a private registration record), so
the hash cannot appear inside the file it describes.

## Context, goal and boundaries

SAM 2.1 small-circle gives a good coarse plate centre, but the radial rim measurement lost consistent
rim support on the development clips ([failure analysis](../analysis/M0_RADIAL_GEOMETRY_FAILURE_ANALYSIS.md))
and mask-guided sampling was rejected
([result](../analysis/MASK_GUIDED_RIM_EXPERIMENT_RESULTS.md)). Test whether two extra image cues improve
the centre estimate over the SAM small-circle baseline: the colour of the plate face, and the hub (bar
end) as a relative-motion cue.

Not included: any held-out, 2026-10-15 or 2023 clip, automatic seeding, a new detector, checkpoint,
dependency, filter, schema or production integration. A measurement-colour model is never fitted on
any clip outside the three development clips below. Raw inputs, authoritative timestamps, display
pixel-centre coordinates, strict finite-value validation and coordinate-free loss are kept.

Implement, evaluate and promote are separate decisions. This plan covers building the tool and, after
a separate owner go-ahead, one evaluation run.

## Fixed configuration

Inputs: `self-back-squat-side-002`, `self-clean-jerk-side-002`, `self-snatch-side-002` only
(`purpose: development`, the only development clips with annotations). The held-out guard is enforced
in code.

Baseline: historical SAM 2.1 small-circle centres. Centre MAE on all labels: 1.922 px (squat), 4.338 px
(clean-jerk), 3.961 px (snatch). Second reference: default radial sampling with SAM ROIs. SAM masks are
produced once with the existing helper, then frozen and hashed; later steps run on the CPU from frozen
masks. Environments are recorded separately (GPU for masks, pinned OpenCV 4.12.0 / NumPy 2.2.6 for
measurement).

Arms, one fixed configuration each:

| Arm | Change | Exactly |
| --- | --- | --- |
| C | edge eligibility on each radial ray | the colour 2, 3, 4, 5 and 6 px **inside** a candidate edge (per-channel median) must match the plate model |
| H | extra relative-motion evidence | frame-to-frame hub displacement fused with the SAM small-circle centres by `fusion.py` |
| C+H | both | Arm C as absolute evidence, hub displacement as relative evidence |

Control (reported for context, not part of the rules): Arm C with the colour model taken from a
different clip. It is expected to reduce coverage.

Plate colour model (C): per-channel median and 1.4826 times the MAD in Lab (OpenCV 8-bit) over the
annulus 0.35 r to 0.85 r around the manual seed on the seed frame, with a scale floor of 1.0. A ray
candidate is eligible only when all three channels satisfy |median - model| / scale < 3.0. The model is
fixed for the whole clip. The strongest eligible gradient is chosen; if none is eligible the ray
supplies no edge. There is no fallback to an ineligible edge or to the coarse centre. Every other
radial default is unchanged (72 rays, 0.5 px step, 60% support, 65% angular coverage, 30% band, fixed
seed radius). With a model that admits every pixel the estimator must reproduce
`vision.radial_center` exactly.

Hub cue (H): grey template of radius 0.12 r (odd side, at least 7 px) around the manual seed on the seed
frame. If the template's grey standard deviation is below 2.0 the hub is unsupported for that clip
(validity guard; the same value as the existing registration `min_std`). On each later frame, normalised
cross-correlation (`TM_CCOEFF_NORMED`) in a window of half-size 0.5 r around the coarse centre; accept
only at NCC >= 0.6, with parabolic sub-pixel refinement. A displacement is emitted only when the hub is
accepted on two consecutive frames; its confidence is the smaller of the two NCC values. A frame without
an accepted hub gives no relative evidence and nothing is interpolated. Fusion uses the existing
`FUSION_CONFIG` defaults.

The manual seed anchors every arm on the seed frame exactly as in `experiment.py`; the seed frame is
excluded from error metrics.

## Metrics

Existing `motion_metrics.evaluate` and `paired`, seed excluded: centre MAE, median and maximum on
labelled frames; displacement MAE on labelled intervals; labelled coverage (share of labels with an
emitted coordinate); losses and silent false-track diagnostics; paired differences against the baseline
on the common support only.

## Decision rules

An arm is **promising** only if all of the following hold:

1. Labelled coverage is at least 80% on each of the three clips.
2. Centre MAE on the common support is lower than the SAM small-circle baseline by at least 0.5 px on at
   least two of three clips, and not worse by more than 0.25 px on the third.
3. Maximum error does not exceed the baseline maximum by more than 2 px.
4. Silent false-track behaviour is not worse than the baseline.
5. No coordinate is emitted on a frame where every evidence gate fails.

For H: if the hub is accepted on fewer than 50% of the labelled frames of a clip, H is **unsupported**
for that clip (not a zero error). Zero available displacement intervals are unsupported, never zero.

The 0.5 px margin is half of the smallest mean disagreement between two label passes seen on existing
clips (1.44 px); a smaller change is indistinguishable from label noise.

Reject an arm that is not promising. Do not infer improvement from incomparable surviving subsets. All
arms are reported whatever the outcome. No multiplicity adjustment is applied; results are descriptive.

## Stop

One run per arm. No threshold, band, template size or model change after seeing a result. On rejection
retain the negative result and do not try variants. Any positive result is development evidence only: it
still needs independent annotation, a complete pipeline and the existing #57 freeze and held-out process.

## What this cannot show

Three clips from one session, all with dark plates. It says nothing about coloured (for example blue)
plates, other gyms or lighting. Label noise is about 1.4 to 3.1 px, so small differences cannot be
resolved. The hub offset from the plate-face centre under perspective is not modelled; H uses only
displacements.

## Ordered work and files

1. Commit this plan and record its SHA-256 (commit message and private `registration.json`) before any
   evaluation.
2. `research/colour-hub-cue/`: `cues.py` (colour model, gated radial sampler, hub template), `clip_core.py`
   (per-clip arms from decoded frames), `run_experiment.py` (registration check, decode, outputs), tests.
3. Synthetic tests: a circular plate with a competing edge of the wrong colour is rejected; unchanged
   results when the model admits everything; hub displacement recovered on a moving synthetic hub; no
   coordinate on absent or weak evidence; byte-identical reruns.
4. Run only after a separate owner go-ahead. Outputs: strict `tracker-prediction-v1` streams and a
   sidecar per arm, evaluated with the existing `experiment.py diagnose`; an aggregate result under
   `docs/analysis/`.

Private masks, frames, coordinates and checkpoints stay out of Git. No schema, golden fixture or ADR
changes are intended. No Rust code is touched.
