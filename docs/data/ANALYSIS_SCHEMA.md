# Canonical analysis schema direction

The Rust types are intentionally minimal during bootstrap. This document describes the persistence/export direction.

## Core entities

### Video metadata

- stable source identifier/hash where appropriate;
- width/height;
- nominal and/or measured timestamps;
- rotation/orientation;
- frame-rate metadata;
- trim range.

### Calibration

- method;
- plate diameter in metres for the initial method;
- measured plate size in pixels;
- reference frame/timestamp;
- scale;
- quality flags.

### Observation

- timestamp;
- raw X/Y pixels;
- target size/bounds if available;
- confidence;
- visibility/tracking state;
- detector/tracker provenance.

### Derived metric sample

- calibrated X/Y;
- velocity;
- acceleration only when validated;
- filter/method version.

### Provenance

- OpenBar pipeline version/commit;
- model identifier/checksum;
- filter and parameters;
- tracker implementation/version;
- calibration method;
- environment/device information needed for reproducibility.

## Export

JSON should be the first canonical interchange format. CSV can be a convenience export for trajectories. Parquet may become useful for research/batch analytics but is not required for M0.
