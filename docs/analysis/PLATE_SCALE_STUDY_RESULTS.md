# Frozen plate-scale diagnostic results (#88)

Plan frozen on 2026-10-06; study completed on 2026-10-07. **REJECT** this tested
median-radius recommendation and empirical p10–p90 band configuration. Equal-video mean
absolute relative scale error against the existing stick point estimates increased from
**1.886% (seed-only) to 1.953% (multi-frame)**. Five of eight videos improved, while three
worsened by more than one percentage point. Only three bands contained their full reference
click-precision intervals. No calibration, tracker/filter default or M0 gate changes.

## Design and evidence binding

The [predeclared plan](../plans/PLATE_SCALE_STUDY_PLAN.md) was committed at `b0546d1`
before multi-frame/reference scoring. Seed-only comparisons had already been inspected;
this is development evidence, not blind or held-out validation. The stdlib diagnostic was
committed before scoring, with the final input-checker repair at `1e14207`.

All eight existing confirmed 2026-10-03 development clips were used. Retained SAM 2.1
bplus-circle `gpu-spike-3` predictions, radius sidecars and canonical analyses supplied the
plate evidence; existing confirmed stick clicks/packages supplied the reference. Media,
confirmed-input and producer hashes, display coordinates, source PTS, exact sample alignment,
seed geometry and canonical observations were checked. The Rust `render` consumer validated
all eight original analyses. No human annotation, recording, seed placement, tracker run,
model or dependency was added. Held-out fixtures were untouched.

The estimator excludes initialization and uses only accepted attempted fits with no rejection
reason, coverage >=18/36, positive inlier count and finite positive radius. Median radius R
gives diagnostic scale D/(2R). Nearest-rank radius p10/p90 gives the reciprocal band
[D/(2 p90), D/(2 p10)]. Rejected/fallback/lost observations remain excluded and counted.
Unsupported cases supply neither a recommended scale nor a band.

## Every available confirmed reference case

Labels follow the earlier VBT audit. Signed error is 100*(plate scale/stick point scale - 1);
negative means the plate estimate is smaller. Mean absolute errors above weight videos equally.
All **5,496 post-seed observations** were eligible: zero lost, fallback or insufficient-geometry
observations, and one excluded initialization per clip. All eight met the fixed support rules.

| Clip | Eligible / post-seed | Seed error % | Multi error % | Band contains point | Overlaps interval | Contains full interval |
|---|---:|---:|---:|---|---|---|
| Clean A | 876/876 | -1.893 | -3.148 | No | No | No |
| Clean B | 755/755 | +2.073 | +3.345 | No | No | No |
| Snatch A | 831/831 | +1.754 | +1.078 | Yes | Yes | Yes |
| Snatch B | 792/792 | +4.469 | +3.352 | No | No | No |
| Squat A | 492/492 | -0.711 | -0.227 | Yes | Yes | Yes |
| Squat B | 373/373 | +1.978 | +3.553 | No | No | No |
| Squat C | 821/821 | -1.376 | -0.728 | Yes | Yes | No |
| Squat D | 556/556 | -0.833 | +0.191 | Yes | Yes | Yes |

| Clip | Diameter median [p10, p90], px | Diameter min–max, px | OLS diameter time slope, px/s | Seed radius deviation % |
|---|---:|---:|---:|---:|
| Clean A | 371.938 [367.298, 374.636] | 358.668–377.634 | +0.146 | -1.279 |
| Clean B | 352.604 [347.934, 357.938] | 338.610–363.996 | -0.561 | +1.247 |
| Snatch A | 359.128 [355.834, 370.056] | 343.610–382.262 | +0.402 | -0.665 |
| Snatch B | 351.901 [346.698, 359.802] | 337.542–368.398 | +0.021 | -1.069 |
| Squat A | 361.677 [357.970, 364.298] | 356.098–366.096 | -0.092 | +0.487 |
| Squat B | 350.882 [347.158, 356.504] | 345.900–359.116 | -0.969 | +1.544 |
| Squat C | 355.920 [353.058, 360.142] | 351.914–363.174 | -0.036 | +0.657 |
| Squat D | 354.064 [351.862, 358.368] | 348.922–363.112 | +0.083 | +1.033 |

Seed radius deviation is 100*(seed radius/median fitted radius - 1). Time slope uses actual
timestamps and is descriptive; it does not identify blur, perspective, depth motion or drift causes.

The existing reference helper models +/-1 px endpoint precision, propagated through the
reciprocal scale. The signed-error ranges below evaluate both reference interval endpoints;
they do not include all physical/reference uncertainties.

| Clip | Seed signed-error range % | Multi signed-error range % |
|---|---:|---:|
| Clean A | [-2.081, -1.704] | [-3.334, -2.962] |
| Clean B | [+1.879, +2.267] | [+3.149, +3.542] |
| Snatch A | [+1.560, +1.948] | [+0.885, +1.270] |
| Snatch B | [+4.270, +4.668] | [+3.156, +3.549] |
| Squat A | [-0.901, -0.520] | [-0.418, -0.035] |
| Squat B | [+1.784, +2.172] | [+3.355, +3.750] |
| Squat C | [-1.569, -1.183] | [-0.922, -0.533] |
| Squat D | [-1.026, -0.639] | [-0.004, +0.387] |

## Decision and limitations

The fixed retain rule required all eight supported, lower equal-video mean absolute error,
at least six strictly improving, no worsening above 1 pp, and full reference-interval coverage
in all eight bands. Only the support condition passed. Maximum worsening was **1.575 pp**;
point/overlap/full-interval coverage was **4/8, 4/8, 3/8**. No thresholds or estimator were tuned.

The empirical band measures central apparent-size spread. Correlated frames and shared fit
bias can produce a narrow band that misses the reference. It is not a confidence interval
or calibrated physical-scale uncertainty and must not be presented as velocity uncertainty.
This rejection applies to this estimator/band configuration on these development clips;
it does not prove seed-only calibration always superior or reject every multi-frame method.
Stick/plate depth differences, perspective, nominal diameter and reference placement uncertainty
remain unquantified. No independent plate-centre or physical velocity accuracy is established.

## Verification and private handoff

The initial freeze failed closed before writing output or scoring: its checker compared the
unrounded trim endpoint to rounded timestamps and excluded the final sample in two snatch clips.
The correction trims source PTS before serialization matching, with a red/green regression.
All eight unchanged real-video inputs then passed. This was a new diagnostic-checker defect,
not a tracker defect or evidence of improved tracking. Rejected GPU rows with null fit counts
are preserved as fallback; partial-write staging supports safe retry. The #103 batch code is unchanged.

Verification: 98 research tests (warnings as errors), 211 validation tests, 12 committed schema
documents, workspace format/clippy/build/tests, tracker/filter/analyze/benchmark smokes,
eight authoritative canonical validations and independent
diff review passed. Two frozen-input CPU passes produced byte-identical reports. All 88 registered
inputs/source/schema/plan/executable files were rehashed unchanged after scoring. This does not
test GPU repeatability: no GPU execution occurred.

Private handoff in the owner's project is `validation/private/plate-scale-study-2026-10-06/`:
frozen inputs, both reports, original non-media input copies, source snapshots, replay command,
verification and file hashes. Media/checkpoints/trajectories stay out of Git. Frozen-input SHA-256:
`b3fbcc1280ad89ff3171c9c502d2835a4c3cf07817f4537545264bb3ff3c1f0c`;
report SHA-256: `dc547b36f0136be882aa83d6c48aa700a5b542941bab8abab386feb6c6617003`.

Stop after this study. Retain `PlateDiameterCalibration@1`; no production scale recommendation
is promoted. M0 remains NO-GO / REWORK. Tracker/filter freeze and independent physical reference
evidence remain unresolved; closing an implementation issue does not satisfy those evidence gates.
