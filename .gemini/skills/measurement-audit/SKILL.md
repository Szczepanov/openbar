---
name: measurement-audit
description: Diff-first reviewer skill for OpenBar code changes. Audits pull requests and branch diffs against non-negotiable measurement invariants, determinism, coordinate conventions, and clean-room constraints.
argument-hint: "Optionally specify branch, commit SHA, or PR diff to review"
---

# Measurement Audit: OpenBar Domain Invariant Review

Perform a diff-first, read-only code review on proposed changes before opening or merging a PR in OpenBar. Audit strictly against core measurement rules, determinism, numerical stability, and clean-room constraints.

## When to Use

- Performing the Phase 7.5 independent review before opening a pull request.
- Reviewing PR diffs touching `crates/openbar-core`, `crates/openbar-tracking`, or CLI pipelines.
- Verifying mathematical correctness, boundary handling, and contract stability.

## Procedure

1. **Obtain the Diff**:
   ```bash
   git diff origin/main...HEAD
   ```
2. **Review Against Non-Negotiable Invariants**:

### Checklist

- [ ] **Raw Observations Preserved**:
  - Filtered/derived kinematics or coordinates sit alongside raw observations in `Analysis` aggregates.
  - Raw observations are never overwritten, smoothed in place, or discarded.
- [ ] **Lost Means No Coordinate**:
  - When tracking confidence drops or the target is lost, coordinates/bounds are absent (`None`).
  - No synthetic/fake coordinates or silent interpolation across tracking gaps.
- [ ] **Timestamps Authoritative**:
  - Time-dependent calculations (velocity, acceleration) use sample timestamps (`timestamp_s`), never frame indices or nominal FPS.
  - Elapsed time $\Delta t$ handles non-uniform intervals; zero or negative $\Delta t$ is handled safely.
- [ ] **Determinism**:
  - Same inputs + pipeline version produce byte-identical output.
  - Serialized maps use `BTreeMap`, never `HashMap`.
  - No wall-clock timestamps, random UUIDs, or thread-scheduling dependencies in measurement artifacts.
- [ ] **Numerical Robustness & Non-Finite Validation**:
  - All floating-point inputs/outputs validate against `f64::is_finite()` (strict rejection of NaN, $+\infty$, $-\infty$).
  - Division-by-zero guards exist for scale factors, time deltas, and normalizations.
  - Confidence values are clamped/validated within `[0.0, 1.0]`.
- [ ] **Coordinate Conventions (ADR-0007)**:
  - Display-oriented: $+X$ right, $+Y$ down.
  - Integer coordinates `(i, j)` lie at pixel centres; `(0, 0)` is the centre of the top-left pixel.
  - Point bounds follow `[0, width)` / `[0, height)`; box bounds follow `[0, width]` / `[0, height]`.
- [ ] **Naming & Units**:
  - Field names have explicit physical unit suffixes (`_px`, `_m`, `_mm`, `_s`).
- [ ] **Error Handling**:
  - Domain errors are hand-written enums implementing `Display` + `std::error::Error`.
  - Fallible constructors use `try_new(...) -> Result<Self, _>`.
- [ ] **Contracts & Golden Files**:
  - Serialized structs enforce `#[serde(deny_unknown_fields)]` and integer version constants.
  - Golden file `analysis-v1.golden.json` is not modified without an intentional, documented schema version bump.
- [ ] **Clean-Room & Legal Discipline**:
  - MIT licence compliance; no third-party code with conflicting licences.
  - Strict compliance with [docs/clean-room/COMPETITOR_BOUNDARIES.md](file:///c:/Users/mdszc/Downloads/projekty/openbar/docs/clean-room/COMPETITOR_BOUNDARIES.md).
  - No media (`*.mp4`), weights, or credentials committed.
- [ ] **M0 Scope Boundary**:
  - Pre-alpha headless engine only. No UI/Flutter, cloud, live camera, or speculative dependencies.

## Output Format

Report findings categorized by severity:
- **CRITICAL / BLOCKING**: Invariant violations (e.g. overwriting raw data, missing NaN checks, non-deterministic serialization, unlicensed code).
- **HIGH**: Numerical edge cases, unhandled division-by-zero, missing error paths.
- **MEDIUM / SUGGESTION**: Idiomatic Rust improvements, naming consistency.
- **VERDICT**: `PASSED` or `CHANGES REQUESTED` with concise remediation steps.
