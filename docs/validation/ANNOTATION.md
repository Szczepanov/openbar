# M0 plate-centre annotation contract and workflow

Issue: #4

## Purpose

OpenBar tracker benchmarks need ground truth whose meaning is stable across decoders,
annotation tools, and future pipeline versions. Annotation v1 therefore makes timestamp,
coordinates, missingness, provenance, and source identity explicit instead of relying on
implicit frame-number conventions.

The machine-readable contract is:

`validation/schema/annotation-v1.schema.json`

Repository-owned validation/import tooling is:

`validation/tools/annotations.py`

The tool uses only the Python standard library. No external annotation application or
library is part of the canonical contract.

## Canonical document

Each annotation document describes exactly one fixture and records:

- `schema_version`;
- `fixture_id`, which must resolve to exactly one entry in a fixture manifest;
- source video SHA-256 when the fixture manifest has one;
- display-oriented coordinate-system metadata;
- timestamp semantics and decoder matching tolerance;
- annotator/tool/version provenance;
- an ordered list of annotation samples.

Public fixture annotations live under:

`validation/fixtures/public/annotations/`

Private/local annotations use the same schema but stay under ignored
`validation/private/` paths beside the private fixture material. Annotation JSON does not
change the redistribution status of the underlying video.

## Coordinate convention

Annotation coordinates are **decoded display pixels after rotation metadata has been
applied**.

For v1:

- integer coordinates are pixel centres: pixel `i` spans `i - 0.5 .. i + 0.5` (ADR-0007);
- origin `(0, 0)`: the centre of the top-left pixel of the display frame, so the frame's
  top-left corner is `(-0.5, -0.5)`. This matches the M0 manual-target seed and the trackers;
- +X: right;
- +Y: down;
- fractional coordinates are allowed;
- valid centre coordinates satisfy `0 <= x < width` and `0 <= y < height`;
- `coordinate_system.rotation_applied` is always `true`.

The validity window `0 <= x < width` is the v1 acceptance rule. It is not the raster extent,
which is `-0.5 <= x < width - 0.5`. ADR-0007 explains why v1 keeps the window. Labelling tools
should emit centres in `0 <= x < width - 0.5`, which is valid under both. The serialized
`coordinate_system.origin` value `top_left` names the top-left pixel; it does not mean the
outer corner.

ADR-0007 treats this as a v1 contract erratum because repository-owned producers and fixtures
already use pixel centres. If an externally authored v1 file followed the old "top-left corner"
wording literally, its convention is ambiguous from JSON alone. Do not auto-shift it by 0.5 px;
confirm its provenance and convert explicitly, or re-label it.

The fixture manifest's `video.width_px` / `video.height_px` describe the encoded raster.
For 90° or 270° rotation metadata, annotation display width/height are therefore swapped.
Rotation values use FFmpeg's counter-clockwise display-matrix convention, normalised modulo
360, the same as the manual seed's `source_rotation_deg` (ADR-0006).
For 0° or 180°, they remain unchanged.

`center_px` means the best manual estimate of the geometric plate centre in the displayed
frame. A partially occluded plate may still be labelled if the centre can be estimated
reliably. If it cannot, use `unlabelable`; do not infer a plausible centre merely to keep
a trajectory continuous.

Optional target size may be represented as exactly one of:

- radius in pixels;
- diameter in pixels;
- display-oriented bounds.

## Time semantics

`timestamp_s` is authoritative. It is the timestamp of the actual decoded frame used for
the annotation, expressed in seconds from media start.

`frame_index` is optional auxiliary lookup information only. It must never be used as the
sole identity of a sample, and OpenBar must not derive annotation time as
`frame_index / nominal_fps`.

This rule is especially important for variable-frame-rate video: use decoder presentation
timestamps (PTS) converted to seconds in a single media-start-relative time base.

If an annotation workflow requests a nominal timestamp but the decoder returns a nearby
frame:

1. record the actual decoded frame timestamp in `timestamp_s`;
2. optionally record the request in `requested_timestamp_s`;
3. require `abs(timestamp_s - requested_timestamp_s)` to be no greater than
   `timebase.decoder_match_tolerance_s`;
4. if no frame can be reproduced inside the declared tolerance, do not pretend that a
   different frame is the requested one.

The repository validator enforces this tolerance and requires strictly increasing unique
actual timestamps.

## Visibility and missingness

Two fields are intentionally separate:

### `annotation_state`

- `labelled` — a centre is present and is considered usable ground truth;
- `unlabelable` — the frame was considered, but a reliable centre cannot be assigned;
- `not_annotated` — the timestamp was deliberately included without a ground-truth label (for example under a sparse annotation policy); no centre was attempted.

### `visibility`

- `visible`;
- `partially_occluded`;
- `fully_occluded`;
- `unknown`.

Rules enforced by the validator:

- `labelled` requires `center_px` and quality `high`, `medium`, or `low`;
- a fully occluded target cannot be `labelled`;
- `unlabelable` carries no centre/size and uses quality `unusable`;
- `not_annotated` carries no centre/size and uses quality `not_assessed`.

Unavailable centres are never encoded as `(0, 0)`, NaN, an unexplained sentinel, or an
interpolated value.

## Provenance

Every annotation file records:

- annotator identifier;
- annotation timestamp with timezone;
- method (`manual_plate_centre` in v1);
- tool name and version;
- optional notes.

The annotator identifier may be pseudonymous. The goal is repeatability/auditability, not
collection of personal information.

## Minimal workflow

The repository does not depend on a GUI annotation product for M0. Any local video/frame
viewer may be used as long as the operator can obtain the actual decoded timestamp and
display-oriented pixel coordinates. The canonical handoff is a small CSV plus JSON metadata
sidecar.

### Optional labelling package

`validation/tools/label_package.py` (standard library plus FFmpeg on `PATH`) prepares that
handoff for one fixture:

```bash
python3 validation/tools/label_package.py \
  --manifest validation/private/manifest.json --fixture <fixture-id> \
  --step-s 0.5 [--start-s 21.0] [--end-s 44.5] [--include-frame <seed-frame-index>]
```

It decodes frames the way the OpenBar frame source does (display rotation applied, passthrough
timing, first video stream) and stamps each one with `(pts - start_pts) * time_base`. Frames are
picked on a uniform time grid, not from tracker output, so the labeller is not anchored on what
is being evaluated. The output folder holds the frames, a `metadata.json` sidecar and an
`index.html` page:

- click the plate centre; Shift+click a rim point to record the radius (optional);
- the wheel zooms, right/middle-drag pans, and a loupe magnifies the cursor area;
- set visibility/quality, mark `unlabelable` or skip (`not_annotated`) explicitly;
- integer coordinates fall on pixel centres, matching how the trackers compute positions (for
  example the contrast tracker's centroid); clicks on the outer half-pixel border are ignored so
  every label stays within `0 <= x < width`;
- progress is kept in the browser's local storage, separately for each package, so a second
  annotator pass (`--annotator-id`) starts empty;
- "Download CSV" writes exactly the `import-csv` columns.

Every extracted frame's PTS is checked against the probe, and the media is checked against the
manifest SHA-256, so labels cannot be stamped onto the wrong frame or a different file. Packages
are written to `validation/private/annotations/work/<fixture-id>.<annotator-id>/` for private
fixtures and `target/label-packages/` otherwise.

Fill in `provenance.annotated_at` in `metadata.json` before importing. Frames of a fixture
whose `redistribution_status` is not `allowed` can only be written below the git-ignored
`validation/private/`; the tool refuses any other output directory.

Example inputs:

- `validation/examples/annotation-import-metadata.example.json`
- `validation/examples/annotation.example.csv`

Import deterministically:

```bash
python3 validation/tools/annotations.py import-csv \
  --metadata validation/examples/annotation-import-metadata.example.json \
  --csv validation/examples/annotation.example.csv \
  --manifest validation/fixtures/public/manifest.json \
  --output /tmp/annotation.json
```

Validate canonical JSON:

```bash
python3 validation/tools/annotations.py validate \
  --manifest validation/fixtures/public/manifest.json \
  validation/fixtures/public/annotations/synthetic-clean-side-12.annotation-v1.json
```

The importer rejects unknown/missing CSV columns, malformed target-size representations,
fake centres on missing samples, timestamp-order violations, source-hash mismatches, and
coordinate/orientation inconsistencies.

## Repeatability / annotation-noise check

Repeat a small set of the same actual decoded timestamps without copying the first-pass
coordinates, then compare only timestamps that are `labelled` in both files:

```bash
python3 validation/tools/annotations.py repeatability \
  validation/fixtures/public/annotations/synthetic-clean-side-12.annotation-v1.json \
  validation/fixtures/public/annotations/synthetic-clean-side-12.repeat-b.annotation-v1.json \
  --manifest validation/fixtures/public/manifest.json
```

The committed synthetic workflow check has 10 comparable labelled timestamps and reports:

- mean absolute X disagreement: `0.5 px`;
- mean absolute Y disagreement: `0.4 px`;
- mean Euclidean disagreement: `0.9 px`;
- Euclidean RMSE: `0.948683 px`;
- maximum Euclidean disagreement: `1.0 px`.

The stored report is:

`validation/fixtures/public/annotations/synthetic-clean-side-12.repeatability.json`

This is a **workflow sanity check**, not a scientific estimate of human annotation noise:
the fixture is synthetic and the second pass is not an independent blinded annotator.
Before benchmark claims depend on sub-pixel or similarly tight ground truth, repeat this
procedure on representative real lifting fixtures with independent/repeated human labels.
The tracker gate must not be interpreted as more precise than the annotation process that
produces its reference values.

## Public synthetic workflow fixture

`synthetic-clean-side-12` is a tiny redistribution-safe side-view clip committed only to
exercise the annotation contract end-to-end. It includes visible, partially occluded,
fully occluded, and intentionally unannotated frames.

It is **not** intended to establish tracker accuracy on real lifting footage. Real
validation fixtures remain governed by `FIXTURE_DATASET.md` and require affirmative
redistribution rights before public media is committed.

## External tool policy

No external annotation tool is selected by this issue. If a future PR standardizes on one,
it must document:

- project/source;
- licence;
- export format/version;
- importer mapping into annotation v1;
- compatibility/licensing implications.

Do not make a proprietary export format the only copy of ground-truth annotations.
