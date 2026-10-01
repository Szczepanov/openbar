# Canonical analysis schema

Issue: #9

## Authority

The authoritative M0 analysis representation is the Rust domain model in
`crates/openbar-core/src/analysis.rs`.

JSON is the canonical M0 interchange format, but there is no independent JSON-only domain
model beside Rust. CLI, benchmark integration, rendering, and later UI code should consume
or construct `analysis::Analysis` and its shared nested types.

Current version:

```text
ANALYSIS_SCHEMA_VERSION = 1
```

## Top-level representation

`Analysis` retains seven concerns explicitly:

1. schema/source identity;
2. decoded/display video metadata and selected time range;
3. the #6 `ManualTargetSeed`;
4. the #8 `PlateDiameterCalibration`;
5. timestamped raw tracker observations, including explicit loss;
6. structurally separate calibrated, filtered, and kinematic layers;
7. pipeline/tracker/model/environment provenance needed for reproducibility.

The model intentionally contains no Flutter view state, cloud/database keys, accounts, or
subscription concepts.

## Identity and video metadata

`AnalysisIdentity` carries a stable `source_id` plus optional fixture ID and source SHA-256.
No random run UUID or wall-clock creation timestamp is injected into the deterministic
measurement payload.

`VideoMetadata` records:

- decoded dimensions;
- display-oriented dimensions;
- source rotation (0/90/180/270 degrees);
- timestamp basis;
- optional nominal and measured FPS metadata;
- selected/trimmed decoded-media time range.

Decoded presentation timestamps remain authoritative. FPS is metadata, not the source for
derivatives.

## Manual seed and calibration

The canonical analysis embeds the existing Rust domain types directly:

- `manual_seed::ManualTargetSeed`;
- `calibration::PlateDiameterCalibration`.

It does not restate their fields in a second persistence model.

For calibration whose provenance is `manual_target_seed`, analysis validation requires the
calibration reference timestamp, optional frame index, orientation, coordinate space,
centre, and bounds to match the embedded seed. Calibration must also lie inside the selected
video range and use the same source rotation as the video metadata.

## Raw observations

`RawObservation` separates tracker state from measured coordinates.

Each sample records:

- authoritative timestamp;
- optional auxiliary frame index;
- `tracking_state`: `tracked`, `low_confidence`, or `lost`;
- visibility state;
- an optional measured `PixelObservation`;
- optional target bounds;
- the tracker provenance ID that produced the sample.

A `lost` observation contains no measured coordinate or target bounds. A tracked or
low-confidence observation requires a measured coordinate. Missing tracking therefore never
requires `(0, 0)`, the previous position, interpolation, or another fabricated value.

Measured `PixelObservation` retains timestamp, raw X/Y pixels, and confidence. Confidence
must be finite and in `[0, 1]`.

## Derived layers

Derived data is not written back into raw observations.

### Calibrated trajectory

`CalibratedTrajectory` stores `MetricPositionSample` values produced from the raw measured
samples and the embedded calibration.

Canonical validation requires one calibrated sample per measured raw observation, preserving
timestamp and confidence. X/Y values are checked against the recorded calibration so a
persisted analysis cannot silently claim different calibrated coordinates for the same raw
measurement.

### Filtered trajectory

`FilteredTrajectory` is optional and stores:

- filter implementation name;
- filter version;
- deterministic parameter map;
- filtered metric samples.

Filtered samples remain timestamp-aligned with the calibrated layer. They do not replace it.

### Kinematics

`KinematicTrajectory` is optional and identifies whether it was derived from the calibrated
or filtered position layer. It also records the kinematics implementation/version/parameters.

Position and timestamp remain aligned with the declared input. Velocity components may be
absent when not yet derivable/validated. Kinematic confidence may stay equal to or decrease
from its position input; it cannot silently increase.

## Provenance and configuration

`AnalysisProvenance` retains:

- OpenBar/pipeline version and optional git commit;
- tracker ID, implementation, version, and effective parameters;
- optional model identifier/checksum;
- optional environment/device/decoder fields only where they materially affect
  reproducibility.

Filter and kinematics provenance live beside the layers they produced. Calibration method,
method version, reference, scale, and quality remain inside the authoritative calibration
object.

Configuration maps use ordered `BTreeMap` storage and scalar values (`bool`, integer, finite
float, string) so repeated serialization does not depend on hash-map iteration order.

## Required invariants

Canonical validation enforces, at minimum:

1. raw observations are never overwritten by filtered/derived values;
2. lost samples are distinguishable from measured samples and carry no fake coordinate;
3. raw and derived timestamps are finite, non-negative, and strictly increasing within each
   series;
4. raw measurements remain within the display-oriented frame;
5. confidence is finite and bounded rather than silently coerced;
6. calibrated samples remain consistent with raw observations and calibration;
7. filtered/kinematic layers remain timestamp-aligned with their declared inputs;
8. tracker references resolve to the retained tracker provenance;
9. source/config/provenance identifiers needed for reproduction are retained;
10. the same logical input + effective configuration/version has deterministic serialized
    semantics.

## JSON/versioning policy

### Schema version

`schema_version` is a required integer. M0 readers accept version `1` only and fail closed on
unsupported versions.

### Field and enum stability

Persisted enum values use explicit `snake_case` strings. Within one schema version, field
names, enum strings, units, and semantics are treated as frozen.

Because canonical readers intentionally reject unknown fields, adding/removing/renaming a
persisted field or enum variant, changing units, or changing field semantics requires a new
analysis schema version rather than silent forward reinterpretation.

### Pre-1.0 compatibility

OpenBar is pre-1.0. There is no promise that future schema versions will remain source- or
wire-compatible with v1. Compatibility must be explicit through a reviewed migration at the
load/export boundary. Migrations must preserve raw observations and provenance and must not
recompute measurements silently.

### Non-finite numbers

NaN and positive/negative infinity are invalid canonical analysis values. Validation rejects
non-finite measurements, derived values, FPS/range metadata, confidence, and floating-point
configuration parameters before canonical export. `null` is not a substitute for an invalid
number; optional values use explicit `Option` fields instead.

### Deterministic semantics

For the same validated `Analysis` value and schema implementation, repeated serialization is
deterministic:

- struct field order is fixed by the Rust representation;
- configuration keys use `BTreeMap` ordering;
- sample ordering is timestamp-validated;
- volatile wall-clock/run IDs are not injected into the measurement payload.

This is deterministic application serialization, not a claim of RFC 8785/JCS cryptographic
JSON canonicalization across arbitrary serializers.

## Golden fixture and tests

`crates/openbar-core/tests/fixtures/analysis-v1.golden.json` is the committed v1 semantic
golden fixture. Tests cover:

- JSON round-trip;
- repeated deterministic serialization;
- tracked/lost/low-confidence observations;
- irregular timestamps;
- absence of a velocity layer;
- full filter/kinematics provenance retention;
- invalid and non-finite values;
- schema/provenance consistency.

## Downstream boundaries

- #5 benchmark artifacts remain benchmark-result contracts; they must not become a competing
  canonical lift-analysis schema.
- #12 `openbar analyze` should emit this `Analysis` JSON and record effective runtime config.
- #13 rendering should consume this representation and visualize loss/low confidence without
  recomputing authoritative measurements.
- CSV may later be a convenience trajectory export, but it cannot replace canonical JSON.
- Parquet remains optional research/batch tooling outside M0 requirements.
