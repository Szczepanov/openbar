# M0 manual target initialization contract

Issue: #6

## Purpose

Every M0 tracker needs the same description of the plate the user selected. The manual seed
is therefore a tracker-independent input contract owned by `openbar-core`, not a detector
result or a tracker-specific initialization structure.

The authoritative Rust implementation is:

`crates/openbar-core/src/manual_seed.rs`

## What the seed represents

A seed captures the user's selection at one authoritative media timestamp:

- reference timestamp in seconds;
- optional frame index for auxiliary lookup only;
- plate centre in pixels;
- one canonical plate geometry: radius in pixels;
- coordinate/orientation semantics;
- optional human selection confidence;
- optional notes.

It does **not** contain:

- automatic detector output;
- tracker confidence;
- tracker-refined state;
- filtered coordinates;
- calibrated metre coordinates;
- derived kinematics.

Trackers should consume the seed as immutable input and create their own internal state.
Refinement must never rewrite the original selection.

## Time semantics

`timestamp_s` is authoritative and uses the decoded media timeline. It is not derived from
nominal FPS and is not renormalized to zero when a trim starts.

`frame_index`, when present, is auxiliary information for diagnostics or decoder lookup.
Variable-frame-rate material must still resolve the seed by timestamp.

Seed validation receives the selected video/trim range in the same timeline and requires the
timestamp to fall within that inclusive range.

## Pixel coordinate convention

M0 uses one serialized coordinate convention:

`display_top_left`

Its semantics are:

- coordinates refer to the display-oriented frame **after** source rotation metadata is applied;
- origin is the top-left corner;
- +X points right;
- +Y points down;
- pixel coordinates are continuous floating-point values;
- a centre is inside the frame when `0 <= x < width` and `0 <= y < height`;
- validation frame width/height are the display-oriented dimensions.

`source_rotation_deg` records the source rotation metadata used to obtain that display
orientation and must be one of 0, 90, 180, or 270. A seed is rejected when this does not
match the video context supplied by the caller.

This makes a 90-degree source video unambiguous: apply the source rotation first, use the
rotated/display dimensions, then record the plate centre/radius in that display frame.

## Canonical geometry

The stored target geometry is a circle:

- centre: `x_px`, `y_px`;
- `radius_px`.

Diameter and a square bounding box are explicit derived helpers:

- `PlateTarget::diameter_px()`;
- `PlateTarget::bounding_box()`.

This avoids accepting radius, diameter, and arbitrary tracker boxes as competing
representations of the same manual selection.

The current M0 contract requires the entire selected circle to fit within the known display
frame. A circle may touch an image edge exactly; geometry extending beyond an edge fails
explicitly instead of being silently clipped.

## Validation and diagnostics

`ManualTargetSeed::try_new` and `validate` use `SeedValidationContext` to check both
intrinsic values and video-specific assumptions.

Typed `SeedValidationError` variants cover:

- invalid/zero frame dimensions;
- invalid selected time range;
- negative or non-finite seed timestamp;
- non-finite X/Y;
- non-finite or non-positive radius;
- non-finite or out-of-range human selection confidence;
- unsupported source rotation;
- source-orientation mismatch;
- timestamp outside the selected range;
- centre outside the known frame;
- target geometry extending outside the frame;
- unsupported standalone schema version;
- blank fixture reference.

No coercion, clipping, timestamp substitution, or fake coordinate is performed.

## Serialization

Standalone seed documents use:

- `validation/schema/manual-target-seed-v1.schema.json`;
- `validation/examples/manual-target-seed.example.json`;
- top-level `schema_version = 1`.

`fixture_id` is optional so the same document shape can be used for ad-hoc CLI videos.
When present, it uses the stable fixture ID from the #3 manifest.

Serde support lives on the Rust domain types. The JSON Schema mirrors those types for
validation assets; it is not a separate authoritative domain model. Issue #9 should reuse
`ManualTargetSeed` inside the canonical analysis representation instead of creating a
second seed JSON shape.

Standard JSON cannot encode NaN or infinity. Rust-side validation still rejects non-finite
values before they can be accepted as a valid domain seed.

## Integration boundaries

### Benchmarking (#5)

A benchmark case can pair a fixture with a standalone seed document through `fixture_id`.
The seed remains independent from tracker implementation/configuration.

### Trackers (#7)

Every tracker should accept the same validated `ManualTargetSeed` semantics. Tracker
refinement belongs to tracker state/output, not to the seed.

### Calibration (#8)

Plate-diameter calibration can use `seed.target().diameter_px()` as the observed plate
size. Calibration should retain that the value came from the manual seed/reference
timestamp rather than copying it without provenance.

### Canonical analysis (#9)

The analysis model should embed the same Rust seed type and keep it structurally distinct
from raw tracker observations and downstream derived values.

### CLI (#12)

The CLI may load a standalone seed document or construct the same Rust type from explicit
arguments. It should report typed validation failures rather than silently adjusting input.

## Scope

This contract deliberately does not add automatic target detection, tracker logic, full
camera calibration, or a plate-selection UI.
