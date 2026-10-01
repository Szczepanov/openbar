# Product strategy

## Distribution intent

The intended official OpenBar app may be free for core athlete workflows and later add optional paid capabilities.

The product should remain useful without an account or subscription.

## Candidate free/core capabilities

Subject to technical validation:

- import/record a video;
- plate tracking;
- bar path;
- position/velocity metrics;
- basic rep analysis;
- local lift library;
- basic lift-to-lift comparison;
- portable JSON/CSV export.

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

Keep local analysis independent of cloud availability. If hosted services arrive later, expose them through explicit interfaces so the local measurement engine remains testable and usable on its own.

## AI principle

A language/vision model should explain structured findings where possible, not become the source of truth for measurements.

Preferred future flow:

```text
bar trajectory + pose + phase/events + athlete history
        -> deterministic/validated features
        -> structured findings
        -> optional LLM/VLM explanation
```

## Branding

"OpenBar" is a working name until trademark/domain/App Store checks are completed.
