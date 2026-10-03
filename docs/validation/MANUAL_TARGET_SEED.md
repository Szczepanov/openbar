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

### Confirmed suggestions (#95)

The one-page VBT session (`research/vbt-workflow/vbt_session.py`) may pre-fill the plate circle with
a suggestion (`plate-hough-edge-v1`). A suggestion is only a starting point on the page: it is used
only after the user confirms that clip, either unchanged or after dragging the centre or rim. A
confirmed circle is therefore still the user's selection under this contract, and the seed is built
by the same `annotations.build_seed` code path as `annotations.py seed`. The schema is unchanged. The
notes record the provenance after the usual CSV hash, annotator and tool sentence:

```text
VBT session <session> page <page id> (vbt_session.py, #95): plate centre accepted|adjusted|manual,
plate radius accepted|adjusted|manual; suggestion <method>:<12 hex> (method <method>, confidence <c>).
```

`accepted` means the suggestion was confirmed unchanged, `adjusted` that the user moved it, and
`manual` that there was no suggestion and the user clicked the item. In the last case the notes name
the failed method and its reason instead of a suggestion id. A seed is never created from a
suggestion without that per-clip confirmation; automatic seeding stays out of scope.

## Creating a seed from one clicked frame

Build a single-frame labelling package, open its `index.html`, then convert the downloaded CSV:

```bash
python validation/tools/label_package.py --manifest validation/fixtures/public/manifest.json --fixture synthetic-clean-side-12 --frame-index 0 --annotator-id seed --output-dir target/seed-pkg
# In index.html: click centre, Shift+click rim, press 1/2/3 for quality, download CSV.
python validation/tools/annotations.py seed --manifest validation/fixtures/public/manifest.json --metadata target/seed-pkg/metadata.json --csv target/seed.csv --output target/seed.json
python validation/tools/schema_check.py --schema validation/schema/manual-target-seed-v1.schema.json target/seed.json
```

Use `--at-s <seconds>` instead of `--frame-index` to select the nearest decoded frame within
the media range. Exactly one of `--step-s`, `--frame-index`, or `--at-s` is required.
Single-frame modes reject the grid-only `--start-s`, `--end-s`, and `--include-frame` flags.
All modes reuse the same extraction and PTS alignment checks. For private videos use the
private manifest and write packages beneath `validation/private/`.

The seed uses the first labelled CSV row in strictly increasing timestamp order with both a
centre and a radius; its frame index is required. Diameter/bounds-only rows do not qualify.
Timestamp and frame index come directly from the label. For VBT, select a frame **before the
first rep**, because trackers run forward from the seed.

The metadata must identify the manifest fixture and its video SHA-256, have display-oriented
dimensions matching that fixture, and declare `decoded_display_pixels`, `top_left`, +X right,
+Y down, with `rotation_applied: true`. Only then is `coordinate_space: display_top_left`
emitted. `source_rotation_deg` comes from the manifest video rotation, normalized modulo 360;
package creation already checks it against the probe. The fresh package's
`annotated_at: FILL-AT-IMPORT` is accepted for seed creation; annotation import still requires
a timezone-bearing date.

The command fails closed on a missing centre-and-radius label, malformed CSV, invalid row
state/visibility/quality, non-increasing or non-finite timestamps, missing/invalid frame index,
non-positive or non-finite radius, a centre outside `[0,width) × [0,height)`, a circle extending
outside `[0,width] × [0,height]`, mismatched fixture/hash, or unsupported coordinates/dimensions.
No coordinate or radius is clipped. An existing output is refused unless `--force` is supplied.

`--selection-confidence <0..1>` optionally records explicit human confidence; it must be finite
and in range. Without that flag the field is omitted. Label quality is never converted to a
probability. Notes record the source CSV's SHA-256 and annotator ID, without paths or wall-clock
values. The seed command reads the CSV bytes once and hashes the exact bytes it parses, so the
provenance digest cannot drift from the content used to construct the seed. Identical CSV bytes and
metadata/options therefore produce byte-identical JSON. The seed is accepted directly by
`openbar-cli analyze --seed target/seed.json` with the same fixture.

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
- integer coordinates are pixel centres: pixel `i` spans `i - 0.5 .. i + 0.5` (ADR-0007);
- origin `(0, 0)` is the centre of the top-left pixel, so the frame's top-left corner is
  `(-0.5, -0.5)`;
- +X points right;
- +Y points down;
- pixel coordinates are continuous floating-point values;
- a centre is inside the frame when `0 <= x < width` and `0 <= y < height`;
- target bounds are inside the frame when `left >= 0`, `top >= 0`, `right <= width` and
  `bottom <= height`;
- validation frame width/height are the display-oriented dimensions.

The two inside-the-frame rules are the v1 acceptance window, not the exact pixel-centre geometry.
Point coordinates over the raster occupy `-0.5 <= x < width - 0.5` (and likewise on Y), while the
outer right/bottom raster edges are at `width - 0.5` / `height - 0.5`. ADR-0007 records why v1
keeps its existing windows. Consequently, a v1 target bound may legally reach `right = width` or
`bottom = height`, up to half a pixel beyond the exact outer raster edge; that is compatibility
behaviour, not the preferred output of new producers. The name `display_top_left` refers to the
top-left pixel, not its outer corner.

ADR-0007 treats the wording correction as a v1 contract erratum: repository-owned seeds and
trackers already use pixel centres. A v1 seed authored externally under the old literal corner
wording cannot be distinguished from a pixel-centre seed by its serialized bytes. Do not apply an
automatic 0.5 px correction without provenance; convert explicitly only when the old edge
convention is known, otherwise recreate the seed.

`source_rotation_deg` records the source rotation metadata used to obtain that display
orientation and must be one of 0, 90, 180, or 270. A seed is rejected when this does not
match the video context supplied by the caller. The value follows FFmpeg's display-matrix
convention: degrees **counter-clockwise** applied to the coded frame to get the display frame,
normalised modulo 360. For example, ffprobe's `rotation=-90` is recorded as 270 (ADR-0006).

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
camera calibration, or a plate-selection UI. The #95 research session page only suggests a circle
that the user must confirm per clip (see "Confirmed suggestions (#95)").
