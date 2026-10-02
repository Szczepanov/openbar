# Product strategy

## Distribution intent

The intended official OpenBar app may be free for core athlete workflows and later add optional paid
capabilities.

The product should remain useful without an account or subscription.

## Primary athlete workflow

The core product loop should be:

> choose the exercise → record/import a set → identify or confirm the barbell target → analyze
> locally → review bar path and validated kinematics → save/compare/export.

Exercise selection is product metadata and, later, an input to versioned rep/phase interpretation.
It must not silently change raw tracking, calibration, filtering, base kinematics, confidence, or
recording-support assessment.

See
[ATHLETE_VIDEO_ANALYSIS_WORKFLOW.md](ATHLETE_VIDEO_ANALYSIS_WORKFLOW.md)
for the recommended staged product and architecture path.

## Candidate free/core capabilities

Subject to technical validation:

- import/record a video;
- exercise/set metadata;
- manual target selection with later automatic target proposal;
- plate tracking;
- bar path;
- position/velocity metrics;
- recording-support/confidence visibility;
- basic rep analysis;
- local lift library;
- basic lift-to-lift comparison;
- portable JSON/CSV export.

The initial useful app should analyze a completed recording. Live camera tracking is not required to
support in-app recording.

## Metric posture

Prioritize metrics closest to validated measurement:

- calibrated trajectory;
- displacement / ROM;
- velocity;
- confidence and tracking availability.

Acceleration, automatic phase metrics, and exercise-specific events require dedicated validation.

Force/power, if introduced later, must be labelled as the precise barbell-derived estimate being
calculated, record its assumptions/provenance, and must not be presented as total athlete
mechanical output.

## Candidate paid capabilities later

Possible examples, not commitments:

- advanced longitudinal analytics;
- cloud backup/sync;
- AI-assisted technique explanation grounded in measured features;
- advanced comparison/history;
- coach/multi-athlete workflows;
- reports/team features;
- optional premium models/services.

## Architecture implications

Do not build a backend for M0.

Keep local analysis independent of cloud availability. If hosted services arrive later, expose them
through explicit interfaces so the local measurement engine remains testable and usable on its own.

The product UI should keep attempt/exercise metadata separate from the canonical measurement
artifact so metadata can be corrected and improved interpretation algorithms can reprocess an
existing measurement without rewriting history.

## AI principle

A language/vision model should explain structured findings where possible, not become the source of
truth for measurements.

Preferred future flow:

```text
bar trajectory + pose + phase/events + athlete history
        -> deterministic/validated features
        -> structured findings
        -> optional LLM/VLM explanation
```

## Branding

"OpenBar" is a working name until trademark/domain/App Store checks are completed.
