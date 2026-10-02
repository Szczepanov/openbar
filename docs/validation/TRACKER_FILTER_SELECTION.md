# Held-out tracker/filter selection study

Issue: #57  
Parent decision: #16 / ADR-0008

## Purpose

This study is the evidence boundary for selecting or rejecting the M0 production tracker/filter
candidate. Synthetic and development results are useful engineering evidence, but they are not
sufficient to claim ordinary phone-video tracking accuracy.

The held-out study must answer:

1. whether a tracker satisfies plate-centre MAE < 3 px;
2. whether it satisfies tracking availability > 99%;
3. whether it can remain confidently wrong during fast movement, occlusion or distractors;
4. how the filter configurations already selected inside each #10 family behave on the same real
   annotation reference;
5. which candidate is selected, or whether the current candidates are rejected.

This document does not change the M0 engineering targets. A future target revision needs its own
evidence-backed rationale.

## Current repository status

When this protocol was introduced, the repository had no held-out annotated non-synthetic validation
fixture. The committed public fixture is synthetic and the local real clips described by
M0_PRIVATE_EVIDENCE_REPORT.md are development material.

Therefore this tooling is a prerequisite for completing #57, not evidence that #57 already passed.
Do not publish a selected-candidate result until the real held-out study has actually run.

## Split and freeze rule

The fixture manifest is the split authority:

- purpose=development may be used to inspect failures and tune before freeze;
- purpose=validation is the held-out selection set;
- synthetic validation fixtures are rejected;
- the final held-out set must contain non-synthetic, side-view, fixed-camera clean, snatch and
  back_squat fixtures.

Before opening held-out results, freeze:

- the exact Git commit;
- SHA-256 of the fixture manifest;
- validation fixture IDs;
- both existing tracker candidate configurations;
- per-family filter configurations selected by the #10 filter-experiment;
- original tracker gate targets;
- false-track diagnostic thresholds.

If the code commit or manifest changes after freeze, evaluation fails closed and a new freeze is
required. Do not tune after seeing held-out results and then reuse the same set as though it remained
held out.

## Candidate configurations

The initial tracker candidates are the two existing M0 baselines:

- template-sad-v1;
- local-contrast-centroid-v1.

The study freezes their current effective defaults. If development evidence justifies a different
configuration, hybrid or replacement tracker, make that change before freeze and review it normally.

Filter candidates are not retuned here. The freeze command runs the existing filter-experiment and
captures the selected development configuration for each family:

- raw;
- centered moving average;
- timestamp-aware Savitzky-Golay;
- constant-velocity Kalman.

This preserves the #10 development/held-out separation.

## Required private inputs

Private/non-redistributable media stays below validation/private/ and remains git-ignored.

For every held-out fixture <id>, the tool requires:

    validation/private/manifest.json
    validation/private/media/...                         # referenced by manifest
    validation/private/seeds/<id>.manual-target-seed-v1.json
    validation/private/annotations/<id>.annotation-v1.json
    validation/private/annotations/<id>.repeatability.json

The annotation must use annotation-v1 and contain comparable labelled samples. The repeatability
report must contain matching labelled samples from a repeated or independent annotation pass.

A minimum label count is intentionally not invented. The final review must consider sample count,
exercise balance, conditions and annotation repeatability before interpreting aggregate metrics.

## Workflow

### 1. Freeze before held-out evaluation

    python3 validation/tools/tracker_filter_selection.py freeze \
      --manifest validation/private/manifest.json \
      --output target/m0-selection/freeze-v1.json

The freeze invokes release filter-experiment to capture #10's selected configuration for every
family. Do not inspect held-out benchmark results before this step.

### 2. Fail-closed preflight

    python3 validation/tools/tracker_filter_selection.py preflight \
      --manifest validation/private/manifest.json \
      --freeze target/m0-selection/freeze-v1.json \
      --output target/m0-selection/preflight.json

Preflight rejects:

- synthetic purpose=validation fixtures;
- held-out fixtures that are not side/fixed;
- missing clean/snatch/back-squat coverage;
- missing media hash/path or hash mismatch;
- missing seed, annotation or repeatability evidence;
- zero comparable labels;
- annotation/source-hash mismatch;
- a manifest changed after freeze.

### 3. Evaluate

    python3 validation/tools/tracker_filter_selection.py evaluate \
      --manifest validation/private/manifest.json \
      --freeze target/m0-selection/freeze-v1.json \
      --output-dir target/m0-selection/run

Evaluation uses the release Rust CLI for tracking, benchmark evaluation, calibration and filtering.
Python only orchestrates versioned outputs and computes study-level diagnostics.

The output directory contains private scratch analyses/predictions plus two aggregate artifacts:

    target/m0-selection/run/tracker-filter-selection-evidence-v1.json
    target/m0-selection/run/TRACKER_FILTER_SELECTION_REPORT.md

Keep scratch data out of Git. Aggregate artifacts contain no raw frames or plate coordinates, but
still need privacy review before publication because fixture IDs and condition metadata may identify
private recordings.

### 4. Record an explicit decision

Selection:

    python3 validation/tools/tracker_filter_selection.py finalize \
      --evidence target/m0-selection/run/tracker-filter-selection-evidence-v1.json \
      --tracker <tracker-id> \
      --filter-family <family> \
      --rationale "<evidence-backed rationale>" \
      --output target/m0-selection/final-v1.json \
      --report target/m0-selection/FINAL_REPORT.md

Rejection:

    python3 validation/tools/tracker_filter_selection.py finalize \
      --evidence target/m0-selection/run/tracker-filter-selection-evidence-v1.json \
      --reject \
      --rationale "<why the current candidates are rejected>" \
      --output target/m0-selection/final-v1.json \
      --report target/m0-selection/FINAL_REPORT.md

finalize refuses to select a tracker unless both original tracking gates pass. It intentionally does
not auto-pick among multiple passing trackers or filter families: selection/rejection remains an
explicit engineering decision grounded in the complete evidence.

## Metrics and failure analysis

### Tracker gates

Per-fixture tracker metrics use the existing Rust benchmark semantics. Study aggregation reports
plate-centre MAE, tracking availability and tracked/lost sample counts against the original strict
operators:

    plate-centre MAE < 3 px
    tracking availability > 99%

Exactly 3.0 px or 99.0% does not pass.

### Silent false tracking

For labelled timestamps where a tracker reports tracked, the study also records:

- high-confidence samples (confidence >= 0.8);
- samples among those with plate-centre error > 3 px;
- high-confidence false-track fraction;
- worst tracked error;
- up to ten timestamp/confidence/error examples.

The 0.8 threshold is diagnostic and not a calibrated probability. Aggregate gate pass must not hide a
wrong-target failure mode.

### Filter comparison

Filters remain implemented by openbar-core. For each tracker/filter pair the study runs canonical
analyze and compares filtered metric position with the same manually digitised plate-centre reference
using the analysis' recorded plate calibration.

The result includes comparable sample count, position MAE, position RMSE and maximum position error.
Review this real-data position evidence together with #10's synthetic velocity/peak/lag trade-offs.

This is not independent physical velocity validation. ROM/mean/peak velocity accuracy remains #58.

## Condition breakdowns

Each held-out fixture retains manifest-authored exercise and recording conditions in aggregate
evidence: FPS, motion blur, occlusion, plate visibility, lighting and challenge tags.

Do not collapse these into one headline number when deciding the supported recording envelope.
#53 consumes the frozen candidate from this study and owns evidence-backed promotion of recording
conditions from warning to supported.

If the final set does not meaningfully exercise blur, contrast, plate scale, occlusion, framing and
realistic bar speed, report those dimensions as unknown instead of inventing coverage.

## Runtime

Release tracker runtime is carried forward as candidate trade-off evidence. It does not satisfy the
phone-class processing gate; #59 owns that measurement.

## Publication and privacy

Private media, frames, annotations, seeds and raw predictions are not committed unless redistribution
rights are explicitly reviewed. Before committing aggregate #57 evidence:

1. inspect JSON/report for private names or notes;
2. confirm fixture IDs/metadata are acceptable to publish;
3. do not publish coordinates, frames or private source paths;
4. keep hashes only when publishing them is acceptable;
5. preserve clean-room and licensing requirements for third-party sources.

## Completion criteria

#57 is complete only when a PR contains actual held-out evidence, not merely this tooling:

- real, annotated, non-synthetic held-out evaluation exists;
- manifest/configuration freeze is recorded;
- MAE and availability are PASS/FAIL or explicitly evidence-backed REVISED;
- silent high-confidence false tracking is quantified and discussed;
- a tracker/filter candidate is selected, or current candidates are explicitly rejected;
- aggregate machine-readable evidence and the human report are committed;
- #53 has an unambiguous frozen candidate or a documented blocker because candidates were rejected.

Until then, ADR-0008's M1 no-go remains in force.
