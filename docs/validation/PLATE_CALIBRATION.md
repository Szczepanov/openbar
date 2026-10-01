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

- origin: top-left of the display-oriented frame;
- +X: right;
- +Y: down.

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
