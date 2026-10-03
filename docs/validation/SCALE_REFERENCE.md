# Filmed scale-reference check

Issue: #87

## Purpose

This protocol provides an independent per-video scale check for OpenBar's existing plate-diameter
calibration. It is validation evidence only. It reads
`analysis.calibration.scale.metres_per_pixel` and never writes back to `analysis-v1`, changes a
plate diameter, or changes calibration semantics.

The tool is:

`validation/tools/scale_reference.py`

It is Python standard-library only. Its `package` subcommand delegates frame probing, selection,
decode, extraction and PTS alignment to the single-frame path in
`validation/tools/label_package.py`; there is no second video-decode path.

## Filming protocol

Before the first rep:

1. Put a known physical length in the **same plane as the plate face/sleeve**, at the bar's distance
   from the camera. A rigid 1 m stick is preferred; a tape measure is acceptable if it is straight
   and taut.
2. Hold the reference upright and visible for at least 2 seconds.
3. Keep the camera fixed in the same position, orientation, focal length and zoom for the reference
   and the entire set.
4. Record the known length in metres.
5. Do not move the camera between the reference and the lift.

A reference is invalid and must be discarded rather than corrected when:

- it is not in the plate plane;
- it is tilted relative to the intended measured length;
- the camera moved between the reference and the lift;
- zoom/focal length changed or a camera mode materially changed the imaging geometry;
- either endpoint cannot be placed reliably.

This check does not correct perspective, parallax or lens distortion. It asks whether an
independent object in the plate plane supports the plate-derived scalar scale for that video.

## Build the click package

The scale-reference page is a separate small template,
`validation/tools/scale_reference_page.html`. This is deliberately separate from
`label_page.html`: the established grid labelling UI, CSV columns and package bytes for existing
`label_package.py` arguments remain unchanged.

The command delegates to `label_package.build()`, so `--frame-index` and `--at-s` have exactly
the same single-frame extraction semantics as manual target seeding.

Example with the public synthetic fixture:

```bash
python validation/tools/scale_reference.py package \
  --manifest validation/fixtures/public/manifest.json \
  --fixture synthetic-clean-side-12 \
  --frame-index 0 \
  --known-length-m 1.0 \
  --output-dir target/scale-reference-package
```

Open `target/scale-reference-package/reference.html`, click endpoint A and endpoint B, then
download the CSV. Coordinates use the OpenBar pixel-centre convention. Report validation uses the
ADR-0007 v1 point window `[0,width) x [0,height)` and fails closed outside it.

The package also contains the unchanged delegated `index.html`, frame PNG and annotation
`metadata.json`, plus `reference-config.json` and `reference.html`.

## Click CSV contract

Exactly one data row is accepted, with these columns in this order:

```text
fixture_id,source_video_sha256,package_id,frame_index,timestamp_s,width_px,height_px,known_length_m,point_a_x_px,point_a_y_px,point_b_x_px,point_b_y_px
```

The report command reads the CSV bytes once, hashes exactly those bytes, then parses those same
bytes. It fails closed on malformed CSV, duplicate/additional rows, non-finite values, zero segment
length, non-positive known length, points outside the ADR-0007 v1 point window, or package/video/
frame/dimension/known-length mismatches.

## Uncertainty model

The v1 evidence model treats each clicked endpoint as having bounded **radial precision ±1 px**.

For clicked segment length `d_px`:

```text
endpoint_precision_px = 1
length_uncertainty_px = 2
reference_scale_m_per_px = known_length_m / d_px
reference_scale_uncertainty_m_per_px =
    known_length_m * length_uncertainty_px / d_px^2
```

The `±2 px` length bound is conservative: by the triangle inequality, moving each endpoint by at
most 1 px can change the endpoint-to-endpoint distance by at most 2 px.

The scale uncertainty is a first-order propagation of that bounded length uncertainty. It is not a
confidence interval and makes no claim about a click-error probability distribution.

The comparison is:

```text
reference_to_plate_ratio =
    reference_scale_m_per_px / analysis.calibration.scale.metres_per_pixel

ratio_uncertainty =
    reference_scale_uncertainty_m_per_px /
    analysis.calibration.scale.metres_per_pixel
```

The plate scale is read unchanged from `analysis-v1`; this tool does not invent a plate-scale
uncertainty. The report always presents the ratio as `value ± uncertainty`, never as an exact
ratio. `consistent_with_1` is true only when 1 lies within that symmetric uncertainty interval.

## Provenance and fail-closed binding

For every row the report verifies and records:

- SHA-256 of the actual video bytes;
- SHA-256 of the exact analysis bytes;
- SHA-256 of the exact click CSV bytes.

The actual video hash must equal the fixture manifest hash. The package metadata/config and click
CSV must name the same fixture and video hash. The analysis must contain matching
`identity.fixture_id` and `identity.source_sha256`. A mismatch is an error, not a warning.

Absolute paths and wall-clock times are intentionally absent from output.

## Report

One or more videos can be reported in one invocation by repeating `--analysis`,
`--package-dir` and `--csv` in corresponding order. Rows are sorted by fixture ID before
serialization, so input order does not affect bytes.

By default output is private and git-ignored:

`validation/private/scale-reference/reports/`

Files:

- `scale-reference-v1.json`;
- `SCALE_REFERENCE_REPORT.md`.

The JSON is **SCHEMALESS by design** because no report instance is committed or catalogued: it is a
private-only/generated evidence artifact under git-ignored `validation/private/` (or an explicit
`target/` directory in tests/demos), and its deterministic structure is exercised by
`validation/tests/test_scale_reference.py`. If a report becomes a committed/validated fixture
type, add a JSON Schema and a `CATALOGUE` entry in `validation/tools/schema_check.py` first.

Example:

```bash
python validation/tools/scale_reference.py report \
  --manifest validation/fixtures/public/manifest.json \
  --analysis target/analyze-smoke.json \
  --package-dir target/scale-reference-package \
  --csv target/scale-reference-click.csv \
  --output-dir target/scale-reference-report
```

## Owner session: exact private workflow

Film the reference as specified above, then keep the original video bytes under
`validation/private/`. Example fixture ID: `owner-snatch-scale-01`.

```bash
# 1. Copy the original recording without transcoding/re-exporting.
mkdir -p validation/private/media
cp /path/to/original.mp4 validation/private/media/owner-snatch-scale-01.mp4

# 2. Probe it and add a private fixture-manifest entry.
python validation/tools/fixture_probe.py draft \
  validation/private/media/owner-snatch-scale-01.mp4 \
  --id owner-snatch-scale-01 \
  --exercise snatch \
  --purpose validation \
  --view side \
  --movement fixed \
  --plate-diameter-m 0.45 \
  --manifest validation/private/manifest.json \
  --notes "Known scale reference filmed in plate plane before first rep."

# 3. Build a single reference frame. Choose a timestamp during the >=2 s reference hold.
python validation/tools/scale_reference.py package \
  --manifest validation/private/manifest.json \
  --fixture owner-snatch-scale-01 \
  --at-s 1.0 \
  --known-length-m 1.0 \
  --output-dir validation/private/scale-reference/packages/owner-snatch-scale-01

# 4. Open reference.html, click the two physical endpoints, and save the downloaded CSV as:
# validation/private/scale-reference/clicks/owner-snatch-scale-01.scale-reference.csv

# 5. Create the normal pre-rep plate seed using the existing #84 path.
python validation/tools/label_package.py \
  --manifest validation/private/manifest.json \
  --fixture owner-snatch-scale-01 \
  --at-s 3.0 \
  --annotator-id seed \
  --output-dir validation/private/scale-reference/seed-package/owner-snatch-scale-01
# In index.html: click plate centre, Shift+click rim, choose quality, download CSV to:
# validation/private/scale-reference/owner-snatch-scale-01.seed.csv
python validation/tools/annotations.py seed \
  --manifest validation/private/manifest.json \
  --metadata validation/private/scale-reference/seed-package/owner-snatch-scale-01/metadata.json \
  --csv validation/private/scale-reference/owner-snatch-scale-01.seed.csv \
  --output validation/private/seeds/owner-snatch-scale-01.manual-target-seed-v1.json

# 6. Run the existing canonical analysis path.
cargo run --locked -p openbar-cli -- analyze \
  --manifest validation/private/manifest.json \
  --fixture owner-snatch-scale-01 \
  --seed validation/private/seeds/owner-snatch-scale-01.manual-target-seed-v1.json \
  --plate-diameter-m 0.45 \
  --tracker template \
  --filter raw \
  --kinematics-max-gap-s 0.2 \
  --kinematics-min-confidence 0 \
  --output validation/private/scale-reference/owner-snatch-scale-01.analysis-v1.json

# 7. Generate the independent scale evidence report.
python validation/tools/scale_reference.py report \
  --manifest validation/private/manifest.json \
  --analysis validation/private/scale-reference/owner-snatch-scale-01.analysis-v1.json \
  --package-dir validation/private/scale-reference/packages/owner-snatch-scale-01 \
  --csv validation/private/scale-reference/clicks/owner-snatch-scale-01.scale-reference.csv
```

Do not commit anything below `validation/private/`. The manual acceptance item remains open until
a real owner session is filmed and measured.

## Integration point

This PR intentionally does not edit the parallel #86/#92 workflow files. A future
`analyze_lift.py` run record may reference the generated scale-reference report by path/hash as
separate evidence; it must not use that report to silently alter analysis calibration.
