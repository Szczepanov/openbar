# Radial geometry failure analysis on existing development footage

Date: 2026-10-05. Follow-up to the
[first development experiment](M0_BAR_PATH_MEASUREMENT_DEVELOPMENT_RESULTS.md).

## Decision

Keep the current radial configurations rejected for complete-pipeline selection. The immediate
problem is insufficient **consistent rim evidence**, not a demonstrated coarse-localizer failure.
Changing the residual threshold or substituting an ellipse alone is not supported by this replay.

The next focused development experiment should compare existing SAM-mask-guided rim sampling with
the current broad, strongest-gradient-per-ray sampling. Reuse the existing model and mask/contour
tools; do not add a model or change production defaults. Marker capture can remain deferred while
this experiment uses the recordings already available.

## Inputs and method

Use only `self-back-squat-side-002`, `self-clean-jerk-side-002` and `self-snatch-side-002` from the
owner checkout's private manifest. These are the three labelled development clips from the first
experiment. The six validation-purpose clips were not opened, decoded or scored. The eight newer
VBT development recordings were inventoried earlier but are not part of this geometry replay.

Replay the four existing configurations: default radial with CSRT ROIs, default radial with SAM
small-circle ROIs, relaxed radial with SAM ROIs, and relaxed/wide-band radial with SAM ROIs. Load
their exact configurations from the retained sidecars and verify the coarse input hashes against
the retained baseline. No settings are tuned in this investigation.

Run the current `vision.radial_center` unchanged under a temporary Python return-event profiler.
Capture its actual edge points, fitted circle and inliers; intercept the existing bilinear sampler
only to retain the sampled intensity profiles. Both hooks are restored after each call. Every
replayed radial diagnostics dictionary matches the historical sidecar exactly after removing
timestamp/frame metadata and the runner's manual-seed annotations.

There are 73 squat, 61 clean and 133 snatch observations in each configuration: **1,068 estimator
calls** across the four configurations. Exclude the manual initialization from all counts below;
the remaining **1,056 calls** measure image evidence. Decode the complete three source clips with
the existing FFmpeg/probed-timestamp boundary, checking media hashes, display metadata, timestamp
uniqueness, seed/frame agreement, decoded counts and decoder exit status.

Private evidence remains under this worktree's
`validation/private/geometry-failure-analysis/`: `run.py`, `summary.json`, per-frame JSON for all
twelve clip/configuration combinations, and six crop grids. The script contains a runnable
synthetic self-check of unchanged estimates, captured support, loss, input preservation and hook
restoration. The summary records source/input hashes, Git state, interpreter/library/decoder
versions and thread settings. Neither private images nor private coordinates are committed.

## Observed rejection stages

Counts are all replayed frames after the seed, **not labelled-frame availability** and not a new
canonical benchmark. The previous report's labelled-frame counts therefore use different denominators.

| Default SAM ROI | Frames after seed | Accepted | Insufficient edge support | Geometry gate | No plausible circle |
| --- | ---: | ---: | ---: | ---: | ---: |
| Squat | 72 | 0 | 45 | 27 | 0 |
| Clean | 60 | 6 | 1 | 53 | 0 |
| Snatch | 132 | 4 | 0 | 128 | 0 |

Default CSRT ROIs produce 0/72, 7/60 and 2/132 accepted estimates respectively. This agreement
between ROI producers, and the retained plate-centred crops, does not establish a need for a
different coarse detector.

The default edge and fitted-circle support requirement is 44 of 72 rays (`ceil(72 * 0.6)`);
angular coverage must reach 16 of 24 occupied sectors (`ceil(24 * 0.65)`).

| Default SAM ROI geometry rejections | Support only | Support + angular coverage | Support + coverage + RMS |
| --- | ---: | ---: | ---: |
| Squat | 0 | 27 | 0 |
| Clean | 2 | 51 | 0 |
| Snatch | 5 | 122 | 1 |

Every geometry rejection fails support. RMS is an additional failure in only one snatch frame;
there are no RMS-only rejections. Raising only `max_residual_px` cannot recover these frames.

Squat has a median 38.5 selected edges, below the 44-ray entry requirement. Across its 5,184
ray attempts, 2,325 fail gradient and/or contrast; 2,052 fail both. This confirms insufficient
edge evidence under the current sampling/thresholds, without establishing whether exposure, blur,
contrast or the chosen sampling band physically caused it.

Clean and snatch have median selected-edge counts of 55 and 59, but median inlier support among
geometry-rejected fits is only 0.181 and 0.486. Many selected strong edges do not agree on one circle.

## Crop inspection and competing hypotheses

The six grids contain six labelled times per clip for default and relaxed SAM ROIs, selected on a
fixed spread through the annotation sequence rather than by best/worst error. Yellow is the fixed
seed radius centred on the coarse observation; cyan is the internal fitted circle, including
**rejected** fits; magenta is the owner's manual centre. Green marks fitted inliers; red marks other
selected edges, or all selected edges when support is too low to attempt a fit. A displayed internal
circle is a diagnostic, not a tracked observation. Unattempted support/coverage is shown as unavailable.

The grids show selected edges on adjacent plate outlines, clothing and equipment, with weak or
missing rim sectors where plate and background have similar luminance. In some clean frames the
fit moves away from the visible plate centre to explain a small subset of those points. These are
observations of selected evidence; they do not independently establish a physical outer-rim reference.

| Hypothesis | Evidence and limit | Priority |
| --- | --- | --- |
| Broad strongest-edge sampling mixes rim and distractor evidence | Selected-point overlays show different outlines; clean/snatch collect enough edges but lose most inlier support | First experiment |
| Too little visible/contrast-qualified rim | Squat fails before fitting on 45/72 frames; most rejected rays fail both intensity tests | Evaluate alongside edge selection |
| Residual threshold is the main problem | No RMS-only default rejection | Do not pursue alone |
| Crop/image clipping is the main problem | No skipped boundary rays in squat/snatch; only 41 of 4,320 ray attempts in default SAM clean, at most seven in a frame | Secondary for clean; not a cross-lift explanation |
| Blur is the dominant physical cause | Selected-edge width is not an independent blur reference; accepted snatch frames have higher median mean edge width (3.61 px) than rejected geometry frames (2.85 px) | Unresolved; no motion-aware weighting yet |
| Circle versus ellipse is the main problem | No independent rim labels or controlled comparison isolates shape mismatch from wrong edge selection | Conditional after boundary evidence improves |

## Bounded alternative-peak diagnostic

For frames with an internal fitted circle, inspect every local gradient maximum in each recorded
ray profile. Apply the original gradient and contrast thresholds and the same subpixel peak
refinement. Count rays with at least one peak within the existing circle's inlier tolerance; retain
the closest passing peak per ray only for computing potential support and sector coverage.

This is an optimistic diagnostic for that **unchanged fitted circle**. It does not refit the circle,
test every possible circle, assess physical plate identity, emit coordinates, or alter lost samples.

| Default SAM ROI | Geometry-rejected frames | Same-circle alternatives reaching support and coverage |
| --- | ---: | ---: |
| Squat | 27 | 0 |
| Clean | 53 | 2 |
| Snatch | 128 | 4 |

Merely trying another passing peak around the already fitted circle is unlikely to repair complete
coverage. Choosing a weaker passing peak when the strongest one fails its intensity checks is also
a small opportunity: only 17 squat, 85 clean and 14 snatch ray profiles contain such alternatives.
Those counts are ray profiles, not recovered frames.

The loss gates also prevent substantial centre errors. On the 19 seed-excluded labelled clean
frames whose default SAM circle is rejected by geometry, internal fitted-centre MAE is **17.28 px**,
with a maximum **46.93 px**. These are hypothetical rejected-fit diagnostics, not predictions or
an accepted candidate's accuracy. Labels remain single-pass development evidence. The result is
enough to reject indiscriminate gate relaxation as a repair.

## Next experiment and stop condition

Use the existing SAM small-mask producer and contour extraction to constrain the natural plate
boundary region before selecting luminance edges. Retain the current radial baseline and the
existing SAM small-circle prediction as separate comparators. Record model/cache/decode provenance;
the historical SAM producer used a JPEG frame cache, so its boundary is not independent ground truth.

Predeclare one bounded boundary-guided configuration before scoring. Keep the current geometry
gates for the first comparison to isolate edge selection. Retain original sampled profiles and
separate refined/lost observations; a mask or coarse centre must not substitute for a missing rim
measurement. Do not feed refinement back into the coarse tracker or interpolate losses.

Evaluate all three development lifts with seed-excluded centre error, displacement error, accepted
frame/label coverage, false-track diagnostics and paired common support. Report every failed frame.
Reject the variant if it improves surviving-error averages without improving squat/clean coverage,
or increases silent centre errors. Better support alone is not promotion evidence.

Only if consistent plate-boundary evidence remains incompatible with circles should a separate,
predeclared circle-versus-ellipse comparison follow. If boundary-guided sampling still cannot supply
enough evidence, retain that negative result and wait for dense labels/controlled capture rather
than expand the model roster. Held-out evaluation, independent physical accuracy and Pixel 8 runtime
remain deferred under their existing prerequisites.

## Verification and reproduction

The pinned research environment is OpenCV 4.12.0 / NumPy 2.2.6. The local replay's synthetic
self-check passes, all 1,068 diagnostics dictionaries match the retained historical results, and
a repeated replay produces byte-identical twelve per-frame JSON files and six crop grids (hashes
retained in local `repeatability.json`). The current research suite passes **79 tests** with
warnings treated as errors; the committed-fixture schema check also passes. No estimator,
prediction, measurement contract, schema, gate or production configuration changed.

Run from this worktree, using the existing owner checkout and prior private experiment artifacts:

```powershell
& C:/Users/mdszc/Downloads/projekty/openbar/research/opencv-tracking/.venv/Scripts/python.exe validation/private/geometry-failure-analysis/run.py
& C:/Users/mdszc/Downloads/projekty/openbar/research/opencv-tracking/.venv/Scripts/python.exe -W error -m unittest discover -s research/bar-path-measurement/tests -p 'test_*.py'
```

The forensic script is retained locally with these private artifacts, not shipped as a new public
tool. Reproduction requires those local inputs. This report is an aggregate research result, not
a candidate freeze or proof of real-world kinematic accuracy.

## Follow-up experiment

The predeclared four-pixel mask-guided comparison is now evaluated in the
[mask-guided rim result](MASK_GUIDED_RIM_EXPERIMENT_RESULTS.md). It fails the complete-candidate
stop condition: no squat coverage recovery, minimal clean coverage change, and insufficient
displacement evidence. Do not treat the recommendation above as an unevaluated reason to keep
tuning this configuration; retain the negative result and obtain the missing development labels.
