# Bar-path measurement: first development experiment

Date: 2026-10-05. Implements the software portion of
[PR #97's plan](../plans/M0_BAR_PATH_MEASUREMENT_EXPERIMENT_PLAN.md).

## Decision

**Do not freeze or promote the current complete candidate.** Circle metrology fails availability
on the represented squat and clean clips. Fusion improves the snatch subset but deliberately cannot
fill absolute loss. Relative displacement shows useful partial results, not a demonstrated complete
replacement. This rejects these tested configurations for selection, not the broader measurement
hypothesis.

No held-out media or annotations were consumed. The research runner refuses validation-purpose
fixtures. The original M0 gates, tracker/filter defaults and production contracts remain unchanged.
The conditional learned-localizer experiment did not start: poor rim geometry with both coarse
producers does not establish a localization/reacquisition bottleneck.

## Inputs and interpretation

Reuse the existing private development recordings and previously produced CSRT and SAM 2.1 small
circle predictions for `self-back-squat-side-002`, `self-clean-jerk-side-002` and
`self-snatch-side-002`. This study's SAM configuration is explicitly the **small** circle baseline;
it does not substitute a different checkpoint or assert that it represents every neural candidate.
The current source checkout begins at merged PR #97, commit `0365cfa`; implementation/helper hashes
and dirty-state metadata accompany the local baseline and output artifacts.

Inputs are hashed and used read-only. The baseline paths are the existing owner checkout's
`target/opencv-spike/phase3-bakeoff-run2/opencv` CSRT outputs and
`target/opencv-spike/phase3-bakeoff` SAM small-circle outputs. Private annotations/seeds/media stay
there; generated predictions, sidecars and reports stay under
`validation/private/bar-path-measurement/` in the implementation worktree. No private coordinates,
images or source measurements are included in this document.

All table errors are Euclidean display-pixel MAE against manual labels, seed excluded. Delta metrics
span actual labelled intervals, not necessarily adjacent frames. Annotation repeatability is not
established on these development clips; no sub-pixel or physical-accuracy claim follows. Sparse
labels cannot establish stationary frame-to-frame jitter or prove the absence of silent errors
between labels. No independent reference, marker recording or Pixel 8 was supplied.

## Baselines

Both existing producers remain available at every evaluated label. Their absolute-centre and
relative-motion rankings differ, as anticipated by the plan:

| Development lift | Labels after seed | CSRT centre MAE px | SAM small circle centre MAE px | CSRT delta MAE px | SAM small circle delta MAE px |
| --- | ---: | ---: | ---: | ---: | ---: |
| Squat | 24 | 3.217 | 1.922 | 2.786 | 2.001 |
| Clean | 20 | 6.435 | 4.338 | 4.854 | 3.951 |
| Snatch | 22 | 7.505 | 3.961 | 2.846 | 4.304 |

In particular, the SAM small-circle baseline has lower absolute error on the snatch but higher
delta error. This is development evidence to investigate, not a filter/tracker selection.

## Bounded geometry experiment

Run the default radial circle configuration with both CSRT and SAM supplying read-only ROIs.
After the default run's availability failure, record and test two further development configurations
with SAM ROIs: a relaxed circle gate and that same gate with a wider radial search band. No change
was made to the coarse inputs, calibration, filtering or normative benchmark definitions.

The relaxed gate overrides only:

- `min_support_fraction = 0.4`;
- `min_arc_fraction = 0.6`;
- `max_residual_px = 1.5`;
- `inlier_tolerance_px = 2.5`.

The wide-band variant additionally sets `radius_band_fraction = 0.45`. Everything else uses the
documented defaults. This is a four-configuration experiment across three clips, not an exhaustive
search; it does not evaluate ellipse or radial-symmetry alternatives.

Default circle metrology with CSRT emits valid absolute observations on 1/73 squat frames, 8/61
clean frames and 3/133 snatch frames, including the manual seed. With SAM, those counts are 1/73,
7/61 and 5/133. Loss primarily records insufficient rim support or the residual/arc/support gate.
The precise physical source of rejected geometry has not been independently isolated.

The relaxed SAM variant increases surviving snatch frames but still has low coverage:

| Lift | Radial labelled availability | Radial centre MAE px | Valid delta intervals | Radial delta MAE px | Fused centre MAE px | Fused delta MAE px |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Squat | 2/24 | 2.286 | 0/23 | unsupported | 2.224 | unsupported |
| Clean | 3/20 | 2.824 | 2/19 | 3.244 | 2.657 | 3.491 |
| Snatch | 14/22 | 2.688 | 6/21 | 2.180 | 2.438 | 1.465 |

These averages cover surviving evidence only. A lower average cannot compensate for losing most
measurements. Fusion also worsens the clean subset's delta error. The wider band does not rescue
coverage: 0/24 squat labels, 3/20 clean labels and 13/22 snatch labels survive. Its snatch fusion
delta MAE is 1.247 px on five intervals, still insufficient complete-pipeline evidence.

## Relative registration

The local DFT translation candidate uses full source-resolution plate-centred patches, crop-origin
mapping, overlap correlation, forward/backward consistency and explicit rejection. Registration
remains vulnerable to plate texture, rotation and background contamination; synthetic numerical
tests are not proof of plate identity in real clips.

For the CSRT ROI runs, compare only intervals where relative registration and each baseline both
have evidence. Baseline values below are recomputed on that same support:

| Lift | Relative labelled intervals | Relative delta MAE px | CSRT delta MAE px on same intervals | SAM small circle delta MAE px on same intervals |
| --- | ---: | ---: | ---: | ---: |
| Squat | 9/23 | 0.958 | 2.485 | 1.151 |
| Clean | 5/19 | 4.023 | 5.279 | 3.721 |
| Snatch | 18/21 | 2.378 | 2.992 | 2.681 |

These partial results justify retaining the displacement experiment for further development.
They do not demonstrate improvement over the best absolute estimator on every represented lift:
the clean result is worse than SAM on shared support. With SAM ROIs, relative interval counts are
10/23, 3/19 and 17/21; the snatch relative MAE (2.451 px) is slightly worse than its SAM baseline on
those same intervals (2.411 px).

Local JSON retains drift, missing edges, loss reasons, correlation diagnostics and confidence/error
bins. No trajectory is integrated through a missing relative edge, and no physical velocity estimate
is inferred from these pixel diagnostics.

## Verification and retained evidence

The new tests cover strict inputs, sparse versus dense labels, timestamp/seed resolution, deterministic
geometry and fusion, fractional translation signs, crop-origin mapping, loss and private/development
boundaries. Existing repository Rust, Python validation, schema and smoke gates pass. CI runs the
isolated research suite with its existing pinned OpenCV environment.

Keep the local baseline JSON, prediction/sidecar hashes, per-clip JSON/Markdown comparisons,
separate runtime sidecars, commands and repeatability report. The original repeatability check reruns
the relaxed SAM snatch experiment and compares absolute/fused predictions and both sidecars byte for
byte. Timing is separate and excludes coarse-production cost, so it is desktop diagnostic evidence
only. Canonical benchmarking/calibration/kinematics are exercised through the existing Rust CLI;
their semantics and public fixtures are unchanged.
The exact measurement-run sources are also retained locally under
`validation/private/bar-path-measurement/source-before-canonical-tail-fix/`; a later canonical
integration fix expands decode selection to include observations beyond the final sparse label
without changing image measurements or the reported development metrics.

### Post-review hardening

PR review tightened confidence/provenance/error semantics without changing the position metrics in
the tables above:

- human `selection_confidence` is no longer published as tracker confidence; the deterministic seed
  observation is emitted at tracker confidence `1.0`, while the original human value is retained
  separately and preserves the historical fusion seed-anchor weight;
- the numerical confidence floor applies only to absolute anchors; a zero-confidence relative edge
  now has zero solve influence, and relative disagreement reduces fused confidence in proportion to
  relative registration confidence;
- run artifacts now record the git commit and dirty state directly;
- shared FFmpeg decoder failures are converted to the runner's normal fail-closed command error.

Accepted `relative_shift` observations already have strictly positive confidence under the configured
registration gates, so removing the relative floor does not change their solve weights. The seed
anchor weight used for the original position solve is also preserved explicitly. Therefore the
reported centre/delta position metrics remain the result of the same numerical position objective.
The emitted seed/fused confidence bytes and provenance do change, so the pre-review byte-repeatability
artifacts are historical evidence only: regenerate current prediction/sidecar hashes before any future
candidate freeze or repeatability claim.

## Phase status and next required evidence

| Plan phase | Status after this experiment |
| --- | --- |
| 0 — baseline/diagnostics/synthetic sanity | Implemented and evaluated on available development inputs |
| 1 — controlled marker | Tracker/protocol prepared and synthetically tested; real capture and offset/depth alignment comparison pending |
| 2 — absolute metrology | Circle implementation/grid evaluated; these configurations rejected for complete-pipeline selection |
| 3 — relative registration | Implemented and evaluated; partial improvements, insufficient cross-lift/coverage evidence for selection |
| 4 — fusion | Conservative deterministic implementation tested and evaluated; no viable complete candidate |
| 5 — learned localizer | Trigger not established; not started |
| 6 — selection/freeze | Not eligible: no sufficiently supported complete candidate; current-byte repeatability must be regenerated after review hardening before any future freeze |
| 7 — held-out | Not run; existing #57 boundary preserved |
| 8 — physical reference/phone runtime | Pending external measurements and a frozen viable pipeline |

First obtain the same-video marker and dense/static development windows to distinguish geometry,
sampling and labelling problems. Investigate the recorded rim failures before adding further models.
Future geometry/registration work must improve the represented clean and squat coverage, not just
the snatch average. Do not tune using error-versus-labelled-velocity correlation.

GitHub's closed #58 state is not independent-reference evidence. The physical-reference study remains
pending according to its [validation contract](../validation/M0_REFERENCE_STUDY.md); production
kinematic accuracy and Pixel 8 runtime are not established by this software work.
