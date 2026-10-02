# Athlete record → analyze workflow

## Purpose

This document defines the intended athlete-facing workflow for OpenBar after the M0 measurement
engine is credible.

The target experience is simple:

> choose what you are lifting → record or import a set → identify/confirm the tracked target →
> analyze locally → review bar path and validated kinematic metrics → save/compare/export.

This is a **product target**, not permission to skip the current M0 validation gates. The mobile
workflow remains downstream of the M0 go/no-go decision in #16.

## Product intent

OpenBar should become useful during ordinary strength and weightlifting practice rather than remain
only a benchmark/CLI project.

A user should be able to analyze a snatch, clean, jerk, squat, deadlift, bench press, press, or other
barbell movement from phone video without needing a dedicated VBT sensor.

The first production workflow should be optimized for **post-set analysis**, not real-time camera
tracking:

1. record a normal video to a file, or import an existing clip;
2. trim/select the useful set;
3. identify the lift as user metadata;
4. identify or confirm the plate/bar target;
5. confirm a valid physical calibration source;
6. run the deterministic OpenBar engine on the recorded file;
7. present trajectory, confidence, recording-support status and validated kinematics;
8. optionally save, compare, export, or revisit the attempt.

Recording directly in the app is compatible with this plan. It does **not** require live tracking:
the camera can record a file first and analysis can run after capture.

## Market reference and clean-room boundary

Public listings for WL Analysis were reviewed only to understand visible market expectations in the
barbell-video-analysis category. Those public descriptions advertise capabilities such as automatic
barbell detection, bar-path visualization, velocity and other derived metrics, charts/statistics,
video comparison, trimming, playback controls, and export.

OpenBar may independently solve the same generic user needs, but this document does not authorize:

- copying WL Analysis UI/layout, wording, icons, assets, interaction details, algorithms, models,
  weights, datasets, or internal data structures;
- decompilation, traffic inspection, binary inspection, or other reverse engineering;
- reproducing undocumented implementation behavior.

OpenBar's implementation remains first-principles and validation-driven.

Public product-behaviour references reviewed:

- Apple App Store: WL Analysis, product description as publicly available on 2026-10-02;
- Google Play: WL Analysis - bar path tracker, product description as publicly available on
  2026-10-02.

No competitor code, model, dataset, or asset is introduced by this proposal.

## Core design rule: exercise-aware workflow, exercise-agnostic measurement

The user may choose an exercise before or after recording, but the selected exercise must not bias
the measurement pipeline.

For the same video, target, calibration, tracker/filter configuration, and pipeline version:

- raw target observations must remain identical;
- calibrated trajectory must remain identical;
- base kinematics must remain identical;
- confidence and recording-support assessment must remain identical.

Changing an exercise label may later change **interpretation** such as rep segmentation, phase
names, or exercise-specific summary metrics. It must not change what the camera measured.

This separation is important for reproducibility and for correcting metadata later. A clip first
saved as "clean" and later relabelled "power clean" should not silently acquire a different bar path.

## Recommended athlete workflow

### 1. New analysis

The primary action should be "New analysis", with two acquisition choices:

- **Record** — use the platform camera to capture a normal video file;
- **Import** — select an existing local video.

Do not make account creation, cloud upload, or subscription state a prerequisite.

### 2. Exercise metadata

Ask the user to choose or search an exercise, with a generic/custom option.

Initial useful vocabulary may include:

- snatch;
- clean;
- jerk;
- clean & jerk;
- front squat;
- back squat;
- deadlift;
- bench press;
- overhead/strict press;
- push press;
- power variants;
- custom barbell movement.

This is product metadata, not automatic exercise recognition. Automatic exercise classification is
not needed for the first useful app.

Optional context may later include load, set/repetition notes, RPE/RIR, athlete, and tags. None of
those should be required to compute the basic trajectory.

### 3. Recording guidance

Before or during capture, give concise guidance based on the evidence-backed recording envelope:

- prefer the validated side-view geometry;
- keep the camera fixed;
- keep the relevant plate/bar visible;
- frame the complete expected movement;
- warn when known unsupported geometry or movement is declared/detected.

Do not invent minimum FPS, yaw tolerance, plate size, camera distance, or occlusion duration until
#53 provides evidence. The app should consume the same recording-support policy as the CLI/core,
not maintain a second set of UI-only thresholds.

### 4. Trim/select useful interval

Allow the user to isolate the set before expensive analysis. This reduces processing cost and makes
target initialization and review easier.

Trimming is media/UI behavior. It must preserve the source identity and selected time range required
to reproduce the analysis.

### 5. Target initialization

Use a staged approach.

#### First app-capable version

Reuse the M0 manual target concept:

- scrub to a clear frame;
- tap the visible plate/bar target;
- adjust target radius/bounds if required.

This is already compatible with the validated core and gives a usable fallback even after
automatic detection exists.

#### Later improvement

Add automatic **target proposal**, not an unreviewable black-box decision:

- detector proposes likely plate/bar target(s);
- user confirms or corrects when confidence is weak or multiple targets exist;
- the confirmed target becomes the seed for the same downstream tracker contract;
- detector/model identity and confidence are recorded as provenance.

Automatic target proposal should be evaluated independently before becoming the default.

### 6. Calibration

For the initial barbell workflow, plate-diameter calibration remains the preferred physical scale.

The UI may make common choices easy, but it must not silently assume a physical diameter. The user
must be able to confirm/correct the calibration source.

Longer term, tracking and calibration should remain conceptually separate so OpenBar can support
other visible targets or reference objects without pretending that an arbitrary tracked object has
a known physical scale.

If a target can be tracked but no valid scale is available, the product may offer clearly labelled
pixel-space visualization, but it must not fabricate metres or m/s.

### 7. Analysis

The recorded/imported clip should flow through the same authoritative Rust measurement logic used
by the CLI:

```text
record/import
    -> media file + authoritative timestamps
    -> recording-support assessment
    -> confirmed target seed
    -> tracking + confidence/loss
    -> calibration
    -> filtering
    -> base kinematics
    -> optional rep/phase interpretation
    -> structured analysis
    -> overlay/charts/export
```

The UI must consume structured results rather than reimplementing filtering, calibration,
kinematics, or event semantics.

### 8. Results screen

The first useful results screen should prioritize measurements that are either already validated or
can be validated directly against M0/M1 reference data:

- video playback with target/path overlay;
- X/Y trajectory;
- vertical and horizontal displacement where recording geometry supports them;
- range of motion;
- velocity over time;
- mean/peak velocity with an explicit metric definition;
- tracking confidence/loss;
- recording-support state and warnings;
- analysis/filter/tracker provenance accessible in details;
- export of the canonical analysis plus user-friendly CSV where applicable.

The path may be colorized by a validated scalar such as velocity later, but the color mapping is a
presentation layer over recorded metrics, not an alternate calculation path.

### 9. Rep and phase analysis

Rep segmentation should be a later deterministic/validated layer over trajectory/kinematics.

Recommended progression:

1. allow manual rep-range selection if a clip contains multiple reps;
2. add deterministic segmentation for simple cyclic lifts;
3. validate exercise-specific phase/event rules;
4. only then expose phase-scoped metrics such as mean concentric velocity or event timing.

For Olympic lifts, phase terminology can become substantially more exercise-specific. Keep those
rules versioned and separable from the base trajectory.

### 10. Save, compare and export

A local lift/attempt library should be a core capability, not a cloud-only feature.

Useful comparison modes later:

- same exercise, two attempts;
- overlaid trajectory;
- side-by-side synchronized video;
- velocity/ROM comparison;
- longitudinal trend summaries.

Comparison must define alignment explicitly. Do not silently time-warp or spatially normalize two
lifts without exposing the method.

## Metric policy

### Tier 1 — prioritize

These are closest to the current measurement chain and should be the first product metrics:

- raw and calibrated X/Y position;
- vertical/horizontal displacement where geometry supports it;
- ROM;
- velocity trace;
- mean velocity;
- peak velocity;
- confidence/tracking availability.

Every velocity summary must define the interval/phase over which it is calculated.

### Tier 2 — add only after dedicated validation

- acceleration;
- automatic rep boundaries;
- concentric/eccentric phase boundaries;
- timing of exercise-specific events;
- trajectory-shape summary features;
- phase-specific velocity metrics.

Differentiation amplifies tracking/filter noise, so acceleration should not be promoted simply
because it is easy to calculate.

### Tier 3 — treat as estimates, not athlete output

Force and power require additional assumptions beyond video position.

If OpenBar later reports them:

- require the inputs and model needed for the estimate;
- version the calculation;
- label the construct precisely, e.g. "barbell mechanical power estimate";
- do not label barbell-derived force/power as total athlete force/power;
- validate the estimate against an appropriate reference;
- make uncertainty and assumptions visible.

The product should prefer fewer defensible metrics over a larger dashboard of plausible-looking
numbers.

## Product/domain separation

Do not force the canonical M0 measurement artifact to become an app database record.

A useful conceptual separation is:

```text
LiftAttempt (product metadata)
  - id
  - source video reference/hash
  - exercise id / custom label
  - optional load / notes / tags
  - capture/import metadata
  - one or more analysis references

Analysis (measurement artifact)
  - source identity + selected time range
  - target seed/provenance
  - recording-support assessment
  - raw observations
  - calibration
  - filtered/derived samples
  - base kinematics
  - pipeline provenance

Interpretation (derived, versioned)
  - rep boundaries
  - movement phases/events
  - exercise-specific metrics
  - interpretation implementation/version
```

This is a direction, not a request to redesign `analysis-v1` during M0.

Benefits:

- exercise metadata can be corrected without changing measured coordinates;
- the same canonical analysis can be reinterpreted by improved rep/phase algorithms;
- multiple analysis versions can coexist for reproducibility;
- local library UX does not leak into the Rust measurement contract.

## Tracking-target scope

Do not prematurely rename the entire M0 plate model into a fully generic object-tracking platform.

Recommended scope:

- **M0 / first product path:** barbell analysis using a visible plate/bar target;
- **architecture:** avoid assumptions that make later target kinds impossible;
- **later:** introduce generic target kinds only when there is a validated calibration/use case.

A generic object path without a physical scale can still be useful visually, but OpenBar's
differentiator should remain trustworthy physical measurement rather than generic video markup.

## Recommended staged delivery after M0

### Stage A — useful offline athlete app

Contingent on the #16 M0 decision:

- Flutter shell;
- local record-to-file and import;
- exercise metadata;
- trim/select interval;
- manual target seed;
- plate calibration;
- offline Rust analysis through a narrow FFI/API;
- path overlay, velocity chart, confidence/support status;
- local save/library;
- JSON/CSV export.

This stage intentionally reuses the proven engine and avoids ML as a dependency for first use.

### Stage B — automatic target proposal

- train/evaluate detector in Python/PyTorch;
- export a reviewed production model through ONNX;
- propose likely target after recording;
- retain manual correction/fallback;
- benchmark detector + tracker end-to-end;
- record model identity/checksum and confidence.

Do not remove manual initialization merely because detection works on average.

### Stage C — reps, interpretation and comparison

- manual then automatic rep boundaries;
- validated exercise-specific interpretation profiles;
- lift-to-lift comparison;
- exercise history and trends;
- optional load-aware summaries.

### Stage D — advanced capture

Only after the offline path is accurate and useful:

- real-time target feedback;
- capture-quality hints derived from validated rules;
- live overlays;
- device-specific performance/battery optimization.

Live tracking should not be a prerequisite for the core record-and-analyze experience.

## Architecture recommendations

1. **Keep Rust authoritative.** Calibration, trajectory, filtering, kinematics, confidence,
   event/rep logic, comparison semantics and production inference orchestration belong in the
   production core.
2. **Use native media/camera APIs where they materially help.** Flutter should orchestrate the
   workflow, not become the video-processing engine.
3. **Keep FFI narrow.** Prefer file/range/config requests and structured result/event streams over
   copying every full frame across Flutter/Rust boundaries.
4. **Preserve timestamps.** Camera/import pipelines must retain authoritative per-frame timing; do
   not derive motion from nominal FPS.
5. **Keep manual fallback.** Automatic detection is a convenience layer over a user-correctable
   measurement process.
6. **Do not let exercise type select hidden tracker/filter settings.** If exercise-specific
   configuration is ever justified, it must be explicit, versioned, benchmarked, and part of
   provenance.
7. **Make support status first-class in UI.** Unsupported geometry must not produce a polished
   metric card that looks equivalent to validated footage.
8. **Prefer asynchronous post-capture processing.** A user-visible progress state is sufficient;
   the core result must remain deterministic for the same inputs/config.
9. **Do not require cloud services for base analysis.** Local analysis and local history are part of
   the core product direction.
10. **Design for reprocessing.** A saved attempt should be able to retain/reuse the source video and
    produce a new analysis under a later pipeline version without overwriting the previous result.

## Validation additions required before product claims

The app workflow introduces additional validation surfaces beyond M0:

- camera-recorded versus imported media parity;
- mobile decode/orientation/timestamp parity with the reference analysis path;
- trim-range correctness;
- FFI serialization/version compatibility;
- detector proposal recall/precision and failure behavior once added;
- end-to-end target + tracker accuracy;
- per-exercise rep/phase event accuracy before exercise-specific metrics are promoted;
- device performance and memory behavior;
- reproducibility of saved/reprocessed analyses.

Product wording should distinguish engineering validation from scientific/biomechanical validation.

## Recommended backlog decomposition

Do not implement this document as one feature PR. After the M0 go/no-go decision, decompose it into
small benchmarkable issues.

Suggested order:

1. product-level attempt/exercise metadata contract;
2. narrow Rust/mobile analysis boundary;
3. import-existing-video vertical slice;
4. record-to-file vertical slice;
5. trim + manual target initialization;
6. results playback/path/velocity/confidence UI;
7. local lift library/export;
8. automatic target-proposal research and benchmark;
9. production detector integration with manual fallback;
10. manual multi-rep range selection;
11. deterministic rep segmentation;
12. exercise-specific interpretation profiles;
13. comparison/history;
14. only then evaluate real-time/live analysis.

Each issue that changes measurement output should inherit the existing validation and provenance
requirements rather than relying on visual acceptance.

## Non-goals for the current M0

This proposal does not reopen M0 scope.

Still out of M0:

- Flutter application development;
- in-app camera recording;
- automatic target detection;
- automatic exercise recognition;
- rep segmentation;
- lift library;
- live tracking;
- force/power productization;
- cloud/backend/auth/subscriptions.

M0 remains responsible for proving the measurement chain on recorded video first.

## Decision summary

OpenBar should explicitly target the workflow the product owner described: record/import a set such
as a snatch or squat, track the barbell/plate, calculate defensible path and velocity metrics, and
make the analysis easy to review and compare.

The shortest credible path is **not** to jump directly to a live AI camera. It is:

> validated offline engine → thin record/import app → manual/correctable target selection →
> trustworthy results → automatic target proposal → rep/exercise interpretation → advanced/live
> features.

That sequence preserves the current architecture while turning it into a clear athlete product.
