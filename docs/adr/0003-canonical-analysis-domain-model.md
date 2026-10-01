# ADR-0003: Canonical analysis model includes raw data, confidence, and provenance

- Status: Accepted
- Date: 2026-10-01
- Updated: 2026-10-01 for issue #9

## Context

Trajectory, velocity, later rep segmentation, UI, exports, comparisons, and scientific
validation all need one stable shared representation.

A display-oriented persistence model or separate JSON-only schema would make validation,
reprocessing, and provenance drift likely. Missing tracking must also be representable
without fake coordinates.

## Decision

The authoritative canonical analysis model is the Rust `analysis::Analysis` aggregate in
`openbar-core`.

It separates:

1. source/video metadata;
2. manual target seed;
3. calibration;
4. raw observations and explicit tracking state;
5. calibrated positions;
6. optional filtered positions;
7. optional kinematics;
8. confidence/quality state;
9. pipeline/tracker/filter/model/environment provenance.

The model embeds the existing `ManualTargetSeed` and `PlateDiameterCalibration` types rather
than copying their serialized fields into parallel domain structures.

JSON is the canonical M0 interchange format. Rust remains authoritative.

## Rules

- Raw observations are never replaced by filtered values.
- `lost` tracking carries no fake coordinate or target bounds.
- Derived values identify the implementation/version/configuration needed to reproduce them.
- Timestamps, not nominal FPS or frame index alone, are authoritative.
- Confidence is validated and cannot silently become certainty.
- Calibrated samples must remain consistent with the raw measurement and recorded
  calibration.
- Filtered and kinematic layers stay structurally separate and timestamp-aligned with their
  declared inputs.
- Volatile run timestamps/random IDs are not part of deterministic measurement output unless
  a later requirement proves they are necessary.
- Configuration maps use deterministic key ordering.
- NaN/infinity are invalid canonical values.

## Versioning

The top-level representation has a required integer `schema_version`.

Version 1 readers fail closed on other versions and on unknown persisted fields. Serialized
enum strings are stable `snake_case` identifiers within the version.

Any persisted shape/enum/unit/semantic change that an existing v1 reader cannot interpret
requires a new schema version. OpenBar is pre-1.0, so compatibility across schema versions is
not implicit; migrations must be explicit, reviewed, preserve raw data/provenance, and must
not silently recompute measurements.

## Determinism

Repeated serialization of the same validated analysis value is deterministic through fixed
Rust field ordering, timestamp-validated vectors, and ordered configuration maps. This is an
application determinism guarantee, not a cross-serializer RFC 8785/JCS guarantee.

## Consequences

The CLI, renderer, validation integration, and later UI have a single analysis contract to
consume. Additional export formats are adapters around this model, not alternate sources of
truth.

The stricter validation makes malformed persisted analyses fail early rather than looking
plausible, which is consistent with OpenBar's measurement-system failure policy.
