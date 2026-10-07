# Frozen CSRT single-scale diagnostic

2026-10-07; continues #108 under the owner's instruction to proceed without manual work.
The #106 box trace attributed translation-only error to both returned location and size;
it did not establish that removing size adaptation fixes position. Test that question once.

## Scope and prerequisite

Reuse the three clean/snatch/squat pure-translation sequences in the delivered
`controlled-motion-replication-2026-10-06` archive and all six whole/stationary-background
sequences in `stationary-background-2026-10-05`. These nine existing development cases have
33 frames each, unchanged confirmed initialization and known injected translations.
No generated media, real-video inference, new labels, human task, GPU or held-out access.
No filter, calibration, canonical schema, Rust source or producer default changes.

Use the installed OpenCV 4.12.0 / NumPy 2.2.6 CPU environment. Change exactly one public
parameter: `TrackerCSRT_Params.number_of_scales`, from its installed default 33 to 1.
Keep all other installed parameters, decoder, seed-box mapping, appearance confidence and
single-thread setting unchanged. Record every effective parameter. The single-scale stream
gets a distinct research implementation identity; raw baseline predictions stay untouched.

The [public parameter/API documentation](https://docs.opencv.org/4.13.0/d3/de9/structcv_1_1TrackerCSRT_1_1Params.html)
describes the field. Pinned [4.12 scale-estimator source](https://github.com/opencv/opencv_contrib/blob/4.12.0/modules/tracking/src/trackerCSRTScaleEstimation.cpp)
uses a unit factor for the single centre scale; its
[Hann-window helper](https://github.com/opencv/opencv_contrib/blob/4.12.0/modules/tracking/src/trackerCSRTUtils.cpp)
handles one-element dimensions. Those Apache-2.0 sources inform the hypothesis; none is copied.

## Execution and evidence

1. Validate each prior artifact inventory and every consumed media/manifest/seed/reference/
   baseline prediction. Freeze their hashes, this plan, runner/helper sources and installed
   parameters in a private registration **before tracking or scoring**. Refuse held-out cases,
   changed registrations and existing run destinations. Pass the frozen registration's SHA-256
   explicitly in each run command and check it before loading registration, before each case and
   after scoring. Constructor probing uses no images.
2. Reuse `track.track`, observing raw successful valid boxes; loss carries no bounds or centre.
   Run baseline and single-scale once on each case. Require baseline runtime-free prediction
   equality with its historical stream, actual decoded PTS binding and full decoder drain/exit.
3. Reuse `controlled_motion.score`, `common_support` and `box_diagnostics.decompose`. Retain
   all-support and common-support seed-excluded mean/p90/max displacement error, availability,
   >3 px and confidence >=0.8 wrong counts, final/return drift, adjacent errors and box-size ranges.
   Verify decomposition against every supported scorer row. Never interpolate loss.
4. Repeat both configurations once on all nine cases. Require byte-identical runtime-free
   predictions, box traces, scores and summary; runtime is separate. Rehash consumed inputs.
   Retain runner/source snapshots, registration, raw predictions/boxes, reports, repeat evidence,
   commands and SHA-256 inventory under a new private directory in the owner's checkout.

## Decision rule and stop

The narrow scale hypothesis is supported only if every single-scale valid box keeps its initial
width/height, all nine cases retain at least baseline tracked support, nonempty seed-excluded
common-support mean error strictly improves in at least six cases including each
stationary-background case, no case worsens by
more than 1 px, and high-confidence wrong counts do not increase in any case. Otherwise reject
the configuration as a general response to these retained translation failures. Report all cases
even when the rule fails; do not change the rule or parameter after scoring.

This is development/synthetic displacement evidence, not independent absolute centre or physical
velocity truth. Even a supported hypothesis does not select a production candidate, establish
phone feasibility or repair M0's freeze/reference gates. Changing scale search can affect location
learning; any improvement cannot be attributed solely to subtracting a box-size term. Constant
appearance/size and artificial background/cutout cues limit transfer to natural lifts.

Run focused fake-tracker checks for effective parameters, research provenance, raw baseline
preservation, coordinate-free invalid/lost boxes and constructor restoration on failure; then the
existing research suite with warnings as errors, schema check and `git diff --check`. Existing
scorer/decomposition tests remain the numeric contract. Stop after this one nine-case test and
document the outcome; no automatic parameter search or production change follows.

## Pre-review execution and guard amendment

An initial pass began with source/input hash checks but without an independently pinned
registration-file digest. Independent review identified that gap during execution. Preserve its
runner, registration and outputs as preliminary evidence only. Before official A/B replay, fix the
registration guard, hash-link that amendment to the preliminary freeze and rerun the exact same
cases/configurations/metrics/decision rule. The initial scores were seen, so this is transparent
development replay, not an unseen or held-out trial. No measurement/threshold tuning is authorized.
