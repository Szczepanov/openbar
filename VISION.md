# OpenBar vision

## Problem

Video can reveal useful barbell movement information, but most consumer analysis is either visual-only, tied to proprietary systems, or insufficiently explicit about measurement uncertainty.

OpenBar should make phone-video bar-path analysis reproducible, inspectable, portable, and useful without requiring a cloud account.

## Product principles

1. **Measurement before coaching.** The system must measure before it explains.
2. **Local first.** Core analysis should work offline.
3. **Uncertainty is data.** Lost tracking, occlusion, weak calibration, and unsupported recording conditions must be visible.
4. **Raw and derived values coexist.** Never throw away the measurement needed to reproduce a derived metric.
5. **Deterministic core.** The same inputs and pipeline version should produce the same outputs.
6. **Validation is a product feature.** Accuracy claims require fixtures, references, and published methodology.
7. **No false biomechanics precision.** Barbell force/power estimates are not equivalent to total athlete mechanical output.
8. **Portable data.** Users should be able to export analyses without proprietary lock-in.
9. **Thin UI, reusable engine.** Mobile/desktop apps should consume the same analysis core.
10. **Clean-room implementation.** Reproduce useful problem-solving capabilities from first principles, not proprietary implementations.

## Long-term direction

Potential later capabilities include automatic plate detection, rep segmentation, lift comparison, longitudinal analytics, real-time tracking, pose estimation, BLE/VBT sensor fusion, coach workflows, and AI-generated explanations grounded in deterministic measurements.

Those capabilities are downstream of trustworthy trajectory measurement.
