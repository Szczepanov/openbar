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

### Manual target seed

The M0 manual selection is a distinct input domain object, not a tracker observation.

It retains:

- authoritative reference timestamp;
- optional auxiliary frame index;
- manually selected plate centre and canonical radius in display-oriented pixels;
- explicit display/top-left coordinate convention and source rotation;
- optional human selection confidence/notes.

The authoritative Rust type is `manual_seed::ManualTargetSeed`. A standalone versioned
document exists for validation fixtures and CLI/benchmark interchange. The canonical #9
analysis representation should embed/reuse this Rust seed type rather than define a second
seed shape.

### Calibration

- method;
- plate diameter in metres for the initial method;
- measured plate size in pixels;
- reference frame/timestamp;
- scale;
- quality flags.

Where the observed plate size comes from a manual target seed, calibration should retain
that provenance and use the seed's explicit diameter helper rather than silently duplicating
or rewriting the manual selection.

### Observation

- timestamp;
- raw X/Y pixels;
- target size/bounds if available;
- confidence;
- visibility/tracking state;
- detector/tracker provenance.

Tracker observations and tracker confidence are downstream outputs. They must not overwrite
or be written back into the manual target seed.

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
