# ADR-0003: Canonical analysis model includes raw data, confidence, and provenance

- Status: Accepted
- Date: 2026-10-01

## Context

Trajectory, velocity, later rep segmentation, UI, exports, comparisons, and scientific validation all need a stable shared representation.

A display-oriented model would make later validation and reproducibility difficult.

## Decision

The canonical model must separate:

1. source/media metadata;
2. calibration;
3. raw observations;
4. filtered/derived values;
5. confidence/quality flags;
6. events/reps;
7. provenance.

Conceptual example:

```json
{
  "exercise": "clean",
  "loadKg": 120,
  "video": {"fps": 60.0},
  "calibration": {
    "method": "plateDiameter",
    "diameterM": 0.45
  },
  "trajectory": [
    {
      "t": 0.0,
      "rawXPx": 812.4,
      "rawYPx": 603.1,
      "xM": 0.0,
      "yM": 0.0,
      "confidence": 0.98
    }
  ],
  "provenance": {
    "pipelineVersion": "0.1.0",
    "model": null,
    "filter": {"name": "pending-validation"}
  }
}
```

## Rules

- Raw observations are never replaced by filtered values.
- Derived values must identify the method/parameters needed to reproduce them.
- Missing/lost tracking is represented explicitly.
- Confidence must not be silently converted into fabricated certainty.
- Timestamps, not frame indexes alone, are authoritative for derivatives.
