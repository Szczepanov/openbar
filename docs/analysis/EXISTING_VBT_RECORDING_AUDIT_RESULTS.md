# Eight existing VBT development recordings: automatic consistency audit

Date: 2026-10-05. Continues the owner-approved workflow without new manual annotation after the
adjusted #103 merge. Implements the [bounded audit plan](../plans/EXISTING_VBT_RECORDING_AUDIT_PLAN.md).

## Decision

Retain these streams as exploratory development output. Complete reported tracking coverage does
not establish accuracy: CSRT and SAM 2.1 bplus-circle centres disagree substantially on all eight
clips. There is no independent centre truth here to select a winner, identify which disagreement
is a false track, or establish physical velocity accuracy. Do not average the streams or promote
a tracker/filter from this report.

## Verified scope

Audit eight newer VBT development recordings: two cleans, two snatches and four back squats.
Existing confirmed seeds, predictions and canonical Analysis files are read-only. No model or
tracker is rerun, no annotation is generated, and no held-out footage is opened or processed.
The separate [three-clip sparse-label report](AUTOMATIC_DEVELOPMENT_REPORT_RESULTS.md) remains
the available manual-label accuracy evidence; its SAM small model is a different configuration.

All eight media hashes and manifest/display metadata match. Actual FFprobe presentation timestamps
align with all **5,504 frame indices** in each tracker stream (11,008 raw observations across both
trackers). Sixteen prediction streams and sixteen canonical Analysis files pass their existing
contracts. Raw positions, confidence, loss and timestamps match predictions exactly, and canonical
seed content matches the retained seeds. The current confirmed session seed fields also match:
timestamp, frame index, target centre/radius, coordinate space and rotation. No stale seed geometry
or stream/frame misalignment is found. FFprobe alignment is not a new full FFmpeg decoder replay.

The authoritative Rust renderer successfully consumes all sixteen Analysis files. Existing
calibration/filter/kinematic configuration and recording-support warnings remain intact; rendering
does not calculate or repair measurements. Sixteen private SVGs retain these analyses' raw and
derived plots. The audit does not apply a new filter or alter any trajectory.

## Tracker disagreement, not error

Exclude the shared initialization frame. Both methods remain tracked at all **5,496 compared
timestamps** with zero declared loss or unmatched timestamps. Report centre distance on exact
common tracked timestamps. Displacement disagreement is the distance between the two trackers'
pixel deltas, restricted to consecutive decoded frame indices and gaps <=0.2 s; 5,488 intervals
are supported. These diagnostics do not use either tracker as a reference label.

| Development clip | Frames after seed | Mean centre disagreement px | Centre disagreement p90 px | Mean adjacent displacement disagreement px | Maximum adjacent displacement disagreement px |
| --- | ---: | ---: | ---: | ---: | ---: |
| Clean A | 876 | 24.583 | 36.161 | 1.662 | 44.976 |
| Clean B | 755 | 24.331 | 37.066 | 2.044 | 73.598 |
| Snatch A | 831 | 18.452 | 32.881 | 1.803 | 85.717 |
| Snatch B | 792 | 17.547 | 33.346 | 1.877 | 30.000 |
| Squat A | 492 | 8.981 | 12.663 | 1.297 | 9.150 |
| Squat B | 373 | 16.702 | 21.931 | 1.464 | 7.636 |
| Squat C | 821 | 11.312 | 16.045 | 1.391 | 7.243 |
| Squat D | 556 | 15.763 | 21.490 | 1.551 | 8.410 |

P90 uses nearest rank. Centre disagreement reaches 67.760 px in the first snatch clip. Large
centre disagreement can coexist with smaller typical delta disagreement; this does not prove
constant bias or correct motion. Large delta disagreements also occur. Their physical/image cause
is unresolved by this stream audit, and algorithm-specific confidence is not a shared probability.

## Retained evidence and verification

Private script/input hashes, per-frame consistency JSON, summary and plots are retained locally.
The owner handoff is `validation/private/vbt-recording-audit-2026-10-05/index.html` in the main
project folder, with a clip-to-table mapping and separate plots for each method. A runnable synthetic
check covers known constant offset, unchanged input, initialization exclusion, loss, missing timestamps,
large observation gaps and non-finite confidence rejection.
Repeated frozen-input audits produce byte-identical eight consistency reports, sixteen SVGs and
the summary (25 artifacts). The owner copy matches the verified source files.

The adjusted batch cleanup/retry regression passes, and the full research suite passes 85 tests
with warnings as errors. The committed schema check passes 12 documents. No Rust source, dependency,
canonical schema, measurement gate or production configuration changes.

The audit closes an automated consistency gap on eight existing clips without adding an owner
labeling task. It supplies neither dense annotation repeatability nor #57 held-out/freeze evidence.
Further human-free research can use controlled known-transform replay to isolate tracker response;
that would remain a separate predeclared experiment and would not validate physical velocity.
