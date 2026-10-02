# Canonical analysis schema

Issue: #9

## Authority

The authoritative M0 analysis representation is the Rust domain model in
`crates/openbar-core/src/analysis.rs`.

JSON is the canonical M0 interchange format, but there is no independent JSON-only domain
model beside Rust. CLI, benchmark integration, rendering, and later UI code should consume
or construct `analysis::Analysis` and its shared nested types.

`validation/schema/analysis-v1.schema.json` describes the v1 wire shape for non-Rust
consumers and CI. It covers field names, types, enum strings, per-field bounds, the lost-sample
rule and the calibration quality-warning rule. Rust validation alone enforces the cross-field
invariants, including but not limited to:

- seed/calibration agreement and plate-scale consistency;
- coordinates inside the display frame;
- ordered time ranges and strictly increasing timestamps;
- calibrated/raw consistency and layer alignment;
- confidence not increasing through derived layers;
- tracker references resolving.

The schema describes canonical serializer output. It is deliberately stricter than Rust in a few
places Rust tolerates on input:

- explicit `null` for an omitted optional field;
- unknown keys inside `calibration.reference.provenance`, which has no `deny_unknown_fields`.

Apart from those, where the two disagree Rust is authoritative and the schema is the bug. CI
validates the golden fixture against it with `python validation/tools/schema_check.py`.

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

## Future multi-protocol extensibility (post-M0; no v1 change)

The current `analysis-v1` model is intentionally **barbell/M0-specific**. Its required manual
target seed, plate-diameter calibration, tracker observations, and trajectory layers are valid M0
semantics and must not be weakened merely to make hypothetical future protocols fit.

Issues #68 (jump measurement) and #69 (running/sprint measurement) establish future measurement
families with materially different evidence:

- vertical jump can initially be derived from take-off/landing event timestamps without continuous
  tracking or spatial calibration;
- broad jump can initially use a known ground reference plus take-off/landing points;
- sprint split timing can initially use known-distance gates plus crossing timestamps;
- continuous sprint speed may later use a calibrated body trajectory.

Therefore future multi-protocol persistence should introduce an explicit **protocol boundary**
rather than turning today's lift-specific required fields into a large collection of optional
fields.

A conceptual future shape is:

```text
MeasurementArtifact
  identity / source / video timebase
  protocol { id, version }
  evidence: protocol-discriminated evidence
  result: protocol-discriminated result
  provenance
  quality / warnings
```

The corresponding future domain abstraction is conceptually:

```text
MeasurementProtocol
  +-- LiftProtocol
  +-- JumpProtocol
  +-- SprintProtocol
```

This is architectural direction, not a committed Rust trait or schema.

### Shared future concepts

The following concepts are expected to generalize safely across measurement families:

- authoritative media timestamps;
- source/capture identity;
- deterministic implementation/protocol versioning;
- confidence/quality/failure representation;
- calibration provenance where calibration is required;
- retained raw evidence;
- typed events with timing uncertainty/provenance;
- typed metrics with explicit units and metric definitions;
- model/tracker/detector provenance when those components produced evidence.

### Concepts that must remain protocol-specific

Do not assume every future analysis has:

- a `ManualTargetSeed`;
- `PlateDiameterCalibration`;
- a tracker;
- continuous X/Y observations;
- a filtered trajectory;
- velocity samples;
- a rep model.

Likewise, avoid a generic untyped `Map<String, number>` for protocol results. A future schema
should use discriminated, typed protocol results so units, event definitions, required fields, and
validation invariants remain explicit.

### Calibration evolution

Do not rename or generalize `PlateDiameterCalibration` inside schema v1.

Future protocols may introduce additional calibration/reference types, for example:

- known reference length;
- calibrated ground line/gates;
- ground-plane/homography calibration.

Those should coexist behind a future versioned calibration/evidence model. Existing barbell
analysis must retain its exact plate-calibration semantics and validation.

### Event evolution

Future jump/sprint work needs first-class measurement events such as:

- take-off;
- landing;
- sprint start;
- gate crossing.

A future event representation should be able to retain:

- authoritative timestamp;
- optional frame index as auxiliary metadata;
- event definition/type;
- confidence;
- temporal uncertainty or bounded event interval where appropriate;
- manual vs algorithmic provenance;
- implementation/model provenance for automatic detection.

Event-only protocols must be representable without fabricating a trajectory.

### Versioning rule

**No field is added to, removed from, or relaxed in `analysis-v1` for #68/#69.**

When a jump/sprint implementation is ready to persist canonical results, choose one of the following
through an explicit ADR/schema review:

1. introduce a new protocol-aware top-level schema/version and provide an explicit migration/import
   path for lift v1; or
2. introduce a separate protocol artifact envelope that embeds/preserves existing lift analysis
   without silently reinterpreting it.

The choice should be based on real implementation evidence. Migrations must preserve raw evidence
and provenance and must never synthesize missing protocol data.

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

Pixel geometry follows ADR-0007. `PixelObservation` centres and `PixelBoundingBox` edges use one
continuous display-oriented coordinate frame: integer `(i, j)` is the centre of pixel `(i, j)`,
and the outer top-left raster edge is `(-0.5, -0.5)`. The v1 validator intentionally retains its
legacy acceptance windows (`0 <= x < width` for points; `0 <= left` and `right <= width` for
bounds, likewise on Y) rather than silently changing persisted v1 semantics. Those windows can
admit the half-pixel right/bottom compatibility region that exact pixel-centre geometry would
exclude; first-party producers should prefer their intersection with the exact geometry.

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

Position and timestamp remain aligned with the declared input. Velocity components may be absent
when the first sample has no backward segment, when a continuity gap exceeds the configured bound,
or when an endpoint is below the configured confidence threshold. Kinematic confidence may stay
equal to or decrease from its position input; it cannot silently increase.

The canonical M0 constructor `derive_kinematic_trajectory` records
`backward-difference@1` plus `max_gap_s` and `min_confidence` in the layer's
`ImplementationProvenance`. This binds the persisted values to the method/configuration needed to
reproduce them. Canonical metric definitions and interval semantics live in
[`../validation/KINEMATIC_METRICS.md`](../validation/KINEMATIC_METRICS.md).

Validation enforces that binding. A kinematics layer whose implementation is
`backward-difference` must be version `1` with exactly the numeric parameters `max_gap_s` and
`min_confidence`. Its samples must equal a re-derivation from the declared input layer with those
parameters: no velocity at the first sample, across a gap longer than `max_gap_s`, or from an
endpoint below `min_confidence`, and confidence equal to the pair minimum. Any other version, a
missing or unknown parameter, or a mismatched sample makes the analysis invalid. Layers from other
implementations keep only the structural checks above. Canonical JSON parsing enables serde_json's `float_roundtrip` mode so fixed-precision
floating-point values written by OpenBar are recovered exactly on read. Re-derived finite velocity
then uses only the repository's existing narrow canonical floating-point comparison; validation does
not widen its acceptance tolerance as timestamp spacing becomes numerically ill-conditioned.

This tightens what a version `1` reader accepts without changing the serialized shape, so
`ANALYSIS_SCHEMA_VERSION` stays `1`. Layers written by `derive_kinematic_trajectory` are
unaffected.

`min_confidence` is applied as an `f32` and persisted as the shortest decimal that round-trips it
(`0.4`, not `0.4000000059604645`).

## Provenance and configuration

`AnalysisProvenance` retains:

- OpenBar/pipeline version and optional git commit;
- tracker ID, implementation, version, and effective parameters;
- optional model identifier/checksum;
- optional environment/device/decoder fields only where they materially affect
  reproducibility.

For the ADR-0006 process frame source, `openbar-cli analyze` stores the deterministic serialized
`FrameSourceProvenance` document in `environment.decoder`. This keeps the existing analysis-v1
shape while retaining the effective FFmpeg version/argument vectors, source hash, stream metadata,
selected range, and decoder diagnostics. A future structured decoder field would require an
explicit schema-version decision rather than an unversioned shape change.

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
