# M0 plate-diameter calibration

Issue: #8

## Purpose

M0 converts display-space plate displacement in pixels into metres by comparing a known
physical plate diameter with the plate diameter observed at one reference timestamp.

The authoritative implementation is:

`crates/openbar-core/src/calibration.rs`

This is deliberately a **scalar image-plane calibration**, not intrinsic/extrinsic camera
calibration and not a 3D reconstruction model.

## Domain model

The low-level `PlateCalibration` value stores all three reproducibility values:

- known plate diameter in metres;
- observed plate diameter in pixels;
- derived metres-per-pixel scale.

Construction is fallible. Physical diameter, pixel diameter, and the derived scale must all
be finite and positive. Persisted JSON also carries the derived scale, and deserialization
rejects a scale that is inconsistent with the two recorded diameters.

`PlateDiameterCalibration` wraps that scalar value with the information required to explain
where the scale came from:

- calibration method and method version;
- reference timestamp;
- optional reference frame index;
- display coordinate space and source rotation;
- reference centre and measurement bounds;
- provenance linking the measurement to the manual target seed;
- metric coordinate convention;
- quality status and geometry warning flags.

The current method version is
`PLATE_DIAMETER_CALIBRATION_METHOD_VERSION = 1`.

## Manual seed relationship

For M0 the preferred constructor is:

`PlateDiameterCalibration::try_from_manual_seed(...)`

It obtains the observed plate diameter from
`seed.target().diameter_px()` and snapshots the same seed reference metadata used to derive
it:

- authoritative seed timestamp;
- auxiliary frame index;
- seed centre;
- seed-derived bounds;
- `display_top_left` coordinate space;
- source rotation;
- optional human selection confidence and notes.

This makes the origin of the pixel diameter explicit. The calibration does not rewrite the
seed and tracker refinement must not alter this reference provenance.

Issue #9 should embed/reuse `PlateDiameterCalibration` rather than define a second
calibration shape.

## Coordinate conventions

### Raw/display pixels

Manual seeds and raw tracker observations use the #6 display-space convention:

- origin: centre of the top-left pixel of the display-oriented frame (integer coordinates are
  pixel centres, ADR-0007);
- +X: right;
- +Y: down.

Calibration uses only coordinate differences and the seed diameter, so it gives the same result
under any constant pixel offset. It still relies on seeds and observations sharing one convention.

Raw observations remain raw pixels.

### Calibrated metric coordinates

M0 metric coordinates use
`reference_centre_x_right_y_up`:

- origin: the plate centre recorded by the calibration reference;
- +X: right;
- +Y: up;
- units: metres.

For an observation `(x_px, y_px)` and reference centre `(x0_px, y0_px)`:

```text
x_m =  (x_px - x0_px) * metres_per_pixel
y_m = -(y_px - y0_px) * metres_per_pixel
```

The Y sign inversion is therefore explicit rather than hidden inside a generic scalar unit
conversion.

`calibrate_observation` returns a `CalibratedPositionSample` containing both the original
`PixelObservation` and the derived metric X/Y values. It never overwrites the raw
observation.

## Quality and geometry warnings

A mathematically valid plate-diameter ratio is not evidence that the same image-plane scale
is physically valid everywhere in the clip.

`CalibrationQualityStatus` supports:

- `unassessed`;
- `supported`;
- `warning`;
- `unsupported`.

M0 warning vocabulary includes:

- unassessed geometry;
- camera yaw;
- camera pitch/height effects;
- perspective/parallax;
- lens distortion;
- plate foreshortening;
- camera movement;
- zoom changes;
- unsupported geometry.

`CalibrationQuality::unassessed()` intentionally carries
`geometry_unassessed`. A warning or unsupported status must contain at least one warning
reason. Issue #15 can refine the evidence-backed classification without changing the
calibration method.

## Geometry assumptions and limitations

Plate-diameter calibration assumes that the observed diameter provides a useful local
image-plane scale for the plate trajectory. That assumption degrades when any of the
following materially changes the apparent geometry:

### Camera yaw

Moving away from a true side view changes how sagittal horizontal movement projects into
the image. Horizontal displacement must not be treated as physically meaningful merely
because a scalar metres-per-pixel value exists.

### Camera pitch and height

Pitch or a large vertical camera offset can make image-plane scale vary with position.
Vertical and horizontal displacement may no longer share a single physically accurate
scale.

### Perspective and parallax

A scalar calibration is local to the depth/geometry represented by the reference plate.
Movement toward or away from the camera changes apparent size and can bias displacement.

### Lens distortion

Wide-angle or otherwise distorted footage can have position-dependent scale. M0 does not
undistort frames or estimate lens intrinsics.

### Plate orientation and foreshortening

A plate that is not approximately parallel to the image plane may appear smaller or
elliptical. The resulting diameter can bias the derived scale.

### Camera movement and zoom

The calibration assumes a stable imaging geometry. Camera translation, rotation, digital
stabilization effects, or zoom changes can invalidate a scale derived at the reference
timestamp.

These conditions are surfaced as quality/warning state. M0 does not attempt to correct them
with a full camera model.

## Numeric and persistence rules

- core numeric calculations use `f64`;
- units are explicit in field and method names;
- non-finite and non-positive physical/pixel diameters are rejected;
- non-finite pixel displacements and raw observation coordinates are rejected;
- source rotation must be one of 0, 90, 180, or 270 degrees;
- persisted measurement bounds must remain centred on the recorded reference centre and match
  the observed diameter;
- derived metric overflow is rejected;
- metric conversion canonicalizes signed zero to positive `0.0` so equivalent zero
  displacements do not serialize as both `0.0` and `-0.0`;
- a persisted derived scale must match the recorded diameters;
- timestamps remain in the decoded media time base;
- the reference frame index, when present, remains auxiliary;
- JSON is able to round-trip method, version, scale, reference, provenance, coordinate
  convention, quality, and warning state.

## Standard reference case

For a 450 mm plate observed as 244 pixels:

```text
metres_per_pixel = 0.45 / 244
                 = 0.0018442622950819672...
```

A 110 pixel horizontal displacement is therefore approximately
`0.202868852459 m`.

The same 110 pixel movement upward in display pixels produces positive metric Y; 110 pixels
downward produces negative metric Y.

## Scope boundary

This model does not provide:

- intrinsic camera calibration;
- extrinsic camera pose;
- 3D reconstruction;
- perspective correction;
- lens undistortion;
- automatic physical plate-diameter estimation;
- front/rear-view geometry support.

A valid scalar conversion must therefore never be interpreted as proof that the recording
geometry is supported.


## Follow-up calibration research

The accepted M0 method remains the explicit single-reference plate-diameter calibration described
above. Two follow-up experiments are justified by reviewed public VBT/CV projects, but neither
changes the current method without separate evidence and versioning.

### Multi-frame visible-diameter estimation

A future experiment should compare:

1. the existing manual-seed diameter;
2. the median visible diameter over high-confidence tracked frames;
3. a robust confidence-weighted estimate over high-confidence frames.

The experiment must guard against circularity: a tracker that drifts can corrupt both trajectory and
the diameter estimate. Report at minimum:

- physical position/ROM error versus an independent reference;
- visible-diameter distribution and robust spread;
- sensitivity to occlusion, blur, plate rotation/foreshortening and camera motion;
- cases where the multi-frame estimate improves or worsens the single-reference result.

Do not silently vary metres-per-pixel frame by frame. A dynamic or multi-frame calibration would be
a new method with its own method version, provenance and validation.

Visible-diameter variation may also be evaluated as a geometry-quality diagnostic. Large or
systematic changes can be evidence of depth change, perspective, foreshortening, camera movement or
tracker error, but no threshold should be promoted without measured false-positive/false-negative
behaviour.

### Intrinsic/lens-distortion sensitivity

A controlled study may estimate phone-camera intrinsics/distortion using a documented calibration
target (for example chessboard/ChArUco tooling) and compare otherwise identical analyses with and
without image undistortion.

Measure:

- centre/trajectory error by field-of-view position;
- calibrated position error;
- horizontal/vertical ROM error;
- mean/peak velocity error where the reference construct is definition-matched;
- runtime and capture/setup cost.

The product should not require an intrinsic-calibration workflow merely because correction is
technically possible. Promotion requires evidence that distortion materially harms measurements
inside the supported recording envelope and that the chosen correction improves them reliably.

## Image-transform geometry invariant

Any future inference/preprocessing path that crops, rotates, resizes, letterboxes or otherwise
transforms frames must preserve a documented mapping back to the canonical display/source pixel
coordinate space before physical calibration.

In particular, non-uniform X/Y resizing must not be followed by a single scalar metres-per-pixel
conversion in transformed coordinates. Either:

- use a geometry-preserving transform; or
- record an invertible transform and map observations back to canonical coordinates before applying
  the calibration model.

Synthetic transform tests should cover crop, uniform resize, non-uniform resize, rotation and
letterbox mappings before such preprocessing is accepted into a production measurement path.
