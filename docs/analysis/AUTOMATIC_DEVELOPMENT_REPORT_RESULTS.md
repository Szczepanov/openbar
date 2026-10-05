# Automatic development replay without new annotation

Date: 2026-10-05. Owner requested progress without human manual work after #102. Implements the
[batch plan](../plans/AUTOMATIC_DEVELOPMENT_REPORT_PLAN.md) through the existing diagnostics,
canonical analysis and rendering commands. The unfinished pseudo-label proposal draft was removed.

## Result and scope

Three existing labelled development clips replay automatically from hash-verified SAM/CSRT
predictions, confirmed manual seeds and existing sparse annotations. No model, decoder cache,
annotation, seed or threshold was modified. Six canonical Analysis files and six separate diagnostic
SVGs show raw trajectories, calibrated position, velocity, confidence and loss. Raw filtering,
0.2 s maximum gap and minimum kinematic confidence zero are explicit exploratory settings.
No held-out clips were processed; no new manual task is required for these outputs.

| Lift | Tracker | Seed-excluded matched labels | Centre MAE px | Label-interval delta MAE px | High-confidence errors >3 px |
| --- | --- | ---: | ---: | ---: | ---: |
| Squat | CSRT | 24/24 | 3.217 | 2.786 | 5 |
| Squat | SAM | 24/24 | 1.922 | 2.001 | 4 |
| Clean | CSRT | 20/20 | 6.435 | 4.854 | 2 |
| Clean | SAM | 20/20 | 4.338 | 3.951 | 11 |
| Snatch | CSRT | 22/22 | 7.505 | 2.846 | 3 |
| Snatch | SAM | 22/22 | 3.961 | 4.304 | 11 |

All 23/19/21 within-annotation intervals are supported for both trackers. These are sparse labelled
intervals, not dense adjacent-frame velocity measurements. High confidence means the existing
algorithm-specific score >=0.8; its meaning is not calibrated across trackers. The separate
canonical benchmark includes initialization and has different denominators/averages; this table
uses the existing seed-excluded motion diagnostics.

SAM has lower sparse centre error across these clips; CSRT has lower sparse displacement error on
snatch. Neither provides evidence for one validated production pipeline. Preserve both streams;
no averaging, fusion, filter selection or pseudo-ground-truth annotation is justified here.
Canonical analyses explicitly retain recording-support warnings. Their displayed velocity is
an experimental derivative, not independently established physical accuracy.

## Handoff and verification

The local owner report is `validation/private/automatic-development-report-2026-10-05/README.md`
in the main project folder, alongside six plots, canonical JSON and per-clip detailed diagnostics.
The replay wrapper validates all consumed snapshot hashes and identities before report writes,
rejects held-out fixtures/duplicate IDs and refuses an existing destination. After successful
preflight it owns that new destination for the invocation; a later diagnostic/render failure removes
only that incomplete destination so the corrected run can retry the same path. Its default is stdlib
only; optional canonical mode delegates computation/rendering to the existing Rust CLI.

The six focused batch tests and full bar-path research suite pass with warnings as errors; the
committed schema check passes 12 documents. The real batch completes all three benchmarks, six
analyses and six renders. Original raw observations, annotations and blind pages are preserved.
Repeating the batch gives byte-identical 20 JSON/Markdown artifacts. Six renders repeated against
the same analysis input paths are byte-identical; changing the input directory changes only the
SVG's recorded `analysis_path` provenance. Nine canonical JSON files pass their existing schemas.

This completes automation of the available development evidence. It does not complete dense human
repeatability, independent physical-reference validation, #57 candidate freeze or M1 readiness.
Keep this workflow usable without new annotation; expand conclusions only when additional independent
evidence becomes available. Private images, trajectories and input paths remain outside Git.
