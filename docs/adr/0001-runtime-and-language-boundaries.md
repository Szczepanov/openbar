# ADR-0001: Rust production core, Python ML research, Flutter UI later

- Status: Accepted
- Date: 2026-10-01

## Context

The product requires deterministic numeric processing, efficient frame handling, mobile/desktop portability, ML experimentation, and eventually a polished cross-platform UI.

Using a single language for all of those concerns would optimize for uniformity rather than suitability.

## Decision

- Rust is the authoritative production core.
- Python/PyTorch is used for ML research/training/evaluation.
- Trained models should cross into production through a portable format such as ONNX.
- Flutter is the preferred future UI after the headless engine is validated.
- Native platform media APIs may be used for decode/camera/hardware acceleration.
- The first product surface is a CLI/validation harness, not a mobile app.

## Consequences

Positive:

- one measurement implementation across platforms;
- strong numeric/domain testability;
- suitable performance and memory control for future real-time processing;
- Python remains available where ML tooling is strongest;
- UI work cannot accidentally become the source of truth for biomechanics.

Costs:

- FFI/platform integration complexity later;
- Rust CV ecosystem may require selective bindings to native libraries;
- team/contributors need multiple toolchains.

## Revisit when

A spike demonstrates that FFI/media-copy overhead materially outweighs the benefits, or a platform restriction makes the architecture impractical.
