# ADR-0005: M0 tracker and frame boundary

## Status

Accepted for M0 experimentation.

## Context

Issue #7 needs deterministic tracker experiments before OpenBar has selected a production
media/decode stack. Coupling the first comparison directly to OpenCV, FFmpeg, a mobile camera API,
Flutter, or a container format would make measurement validation depend on an unrelated media
decision.

The manual seed and benchmark contracts already live in `openbar-core` and must remain common to
every candidate.

## Decision

Add `openbar-tracking`, a small Rust crate for M0 manual-seed tracker experiments.

Its input boundary is decoder-agnostic:

- display-oriented 8-bit grayscale image access;
- explicit image dimensions;
- authoritative presentation timestamps supplied by the caller;
- optional frame index as auxiliary provenance;
- the canonical `ManualTargetSeed`;
- explicit tracker configuration.

The crate owns no media paths, codecs, cameras, Flutter types, nominal-FPS derivative logic, or
automatic target detection. `GrayFrame` is an owned convenience implementation for tests and
headless experiments; a future media layer can implement the image-view trait without changing
tracker semantics.

Tracker output contains raw pixel coordinates only when actually measured, explicit tracked/lost
state, target bounds when available, implementation/version/config provenance, algorithm-specific
confidence semantics, and diagnostics. It adapts to `openbar-core::benchmark` rather than
reimplementing benchmark metrics.

The first two candidates are dependency-free baselines implemented from first principles:

1. fixed-template matching with normalized mean absolute pixel difference;
2. local-contrast weighted-centroid tracking.

Neither is selected as the production tracker.

## Consequences

- M0 tracker logic is headless and benchmarkable before a media stack is chosen.
- All candidates reuse one seed contract and one timestamp-based evaluator.
- Loss never requires a fabricated coordinate.
- Confidence remains algorithm-specific and is not treated as a calibrated cross-tracker
  probability.
- Real decoded-video fixtures are still required before choosing a production tracker.
- Later optical-flow, detector-assisted, or learned trackers can implement the same boundary.

## Dependency and clean-room note

No external CV library, model, dataset, or proprietary competitor implementation is introduced.
Production `openbar-tracking` depends only on `openbar-core`. Test-only JSON parsing reuses the
workspace's existing `serde` / `serde_json` dependencies.
