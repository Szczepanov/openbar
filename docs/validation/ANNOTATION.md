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

- origin: top-left corner of the display frame `(0, 0)`, matching the M0 manual-target seed convention;
- +X: right;
- +Y: down;
- fractional coordinates are allowed;
- valid centre coordinates satisfy `0 <= x < width` and `0 <= y < height`;
- `coordinate_system.rotation_applied` is always `true`.

The fixture manifest's `video.width_px` / `video.height_px` describe the encoded raster.
For 90° or 270° rotation metadata, annotation display width/height are therefore swapped.
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

The repository intentionally does not select or embed a GUI annotation product for M0.
Any local video/frame viewer may be used as long as the operator can obtain the actual
decoded timestamp and display-oriented pixel coordinates. The canonical handoff is a
small CSV plus JSON metadata sidecar.

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
