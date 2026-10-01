# ADR-0004: Validation-first M0 before automatic detection or mobile UI

- Status: Accepted
- Date: 2026-10-01

## Context

The highest technical risk is not UI construction. It is whether ordinary phone video can yield sufficiently robust and useful bar-path measurements.

Building automatic detection and a polished app before resolving this risk would create substantial sunk cost.

## Decision

M0 uses manual plate initialization and focuses on tracking, calibration, filtering, kinematics, export, and quantitative validation.

Automatic plate detection is a later milestone and should initially propose a plate for user confirmation rather than being silently trusted.

Flutter/mobile implementation starts only after an explicit M0 go/no-go review.

## M0 completion

Given a supported side-view clip, a manually identified plate, and a known plate diameter:

- track the plate;
- produce calibrated X/Y trajectory;
- derive velocity;
- surface confidence/lost tracking;
- preserve raw data;
- render/export results;
- pass deterministic tests;
- publish benchmark results against labelled/reference data.

See `docs/roadmap/M0.md` and `docs/validation/M0_VALIDATION.md`.
