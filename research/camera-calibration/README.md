# ChArUco camera calibration and scale-sensitivity research

Issue: #177. This local experiment measures whether camera distortion correction improves
independent physical-length measurements for one recorded camera configuration. It does not
change M0 calibration, tracking, `analysis-v1`, #79, Rust, or the production recording envelope.
The implementation plan is
[CHARUCO_CALIBRATION_EXPERIMENT.md](../../docs/plans/CHARUCO_CALIBRATION_EXPERIMENT.md).

No real calibration/reference capture set has been supplied for this experiment. Synthetic
tests establish implementation behavior only. Until verified captures pass the frozen gates,
the evidence decision is `defer_missing_captures`, with missing prerequisites recorded.

## Environment and pinned APIs

Reuse `research/opencv-tracking/requirements.txt`: `opencv-contrib-python==4.12.0.88` and
`numpy==2.2.6`. Do not introduce another requirements file or a Rust dependency. OpenCV Python
packaging is MIT, bundled OpenCV is Apache-2.0, and NumPy is BSD-3-Clause; bundled notices apply.
Sources: [OpenCV packaging](https://github.com/opencv/opencv-python),
[NumPy](https://numpy.org/).

The research runner uses the OpenCV 4.12 APIs `cv2.aruco.CharucoBoard`,
`cv2.aruco.CharucoDetector.detectBoard`, `board.matchImagePoints`,
`cv2.calibrateCameraExtended`, `cv2.projectPoints`, and
`cv2.undistortPoints(..., P=K)`. The last call returns corrected pixel coordinates using the
same camera matrix, rather than normalized camera coordinates. Reference:
[OpenCV 4.12 ChArUco calibration tutorial](https://docs.opencv.org/4.12.0/da/d13/tutorial_aruco_calibration.html).

Use the existing research interpreter. For repeatability, set `OPENBLAS_NUM_THREADS=1`,
`OMP_NUM_THREADS=1`, and `MKL_NUM_THREADS=1` before starting it. The runner sets OpenCV threads
to one and RNG seed to zero. Calibration uses the standard five distortion coefficients,
flags `0`, and termination at 100 iterations or epsilon `1e-9`. Reports include the actual
Python/OpenCV/NumPy environment and source/requirements/input hashes. Repeatability is scoped
to the same pinned environment and inputs; different platforms need their own replay check.
ChArUco uses `minMarkers=2`, `tryRefineMarkers=false`, marker corner refinement `0` (none),
and the post-4.6 board pattern (`legacy=false`). Other detector parameters use pinned OpenCV defaults.

## Physical board and camera protocol

Generate a board with the `board` command below, print without fit-to-page scaling, and mount it
on a rigid flat surface. The generated PNG establishes nominal geometry only. It is not evidence
of a printer's physical scale, a measured board, or a usable camera calibration.

Measure several squares across both board axes and record the representative measured side
length and its measurement tolerance in metres. Verify the print scale against the intended
square length and check flatness before setting `print_scale_verified` or `flatness_verified`.
Check marker-to-square proportions too; clipped, stretched, curled, or incorrectly scaled prints
must be replaced. Preserve the board config, generated PNG hash, measurements and verification
notes with the private capture set.

Record device and physical lens, display-oriented resolution/orientation, optical or digital
zoom, locked focus, stabilization setting, camera mode, and additional capture notes. Use the
same settings for fit views, held-out views, and physical references. Photograph the board
at multiple image positions, distances and tilts, keeping it sharply visible; nearly identical
front-facing captures do not constrain a robust intrinsic calibration.

A retained intrinsic calibration may only be considered for the same camera/lens and unchanged
resolution, orientation, zoom, focus, stabilization and mode. Re-capture and re-evaluate after any
change. Matching metadata is necessary, but does not prove imaging geometry stayed fixed:
autofocus, electronic stabilization, lens switching and crop changes can invalidate reuse.
Lock or disable those controls where possible. Camera movement and reference/plate depth are
separate scene-geometry concerns, even if intrinsic settings match.

## Input contract and commands

`board --config CONFIG --output PNG` renders a board. `run --manifest MANIFEST --output REPORT`
performs the bounded experiment. Store real inputs and outputs under git-ignored
`validation/private/camera-calibration/`; `target/` is suitable for synthetic tests. Never commit
private captures, extracted frames, endpoint measurements or report instances.

The version 1 manifest is a research contract, not a canonical fixture/analysis schema. Readers
reject unknown fields/versions, duplicate IDs, nonfinite values, input hash mismatches, incompatible
camera settings, and fitting/held-out input overlap. Image paths must be relative and resolve inside
the manifest directory; absolute paths, parent traversal and escaping symlinks are rejected.
Coordinates refer to the display-oriented image: integer pixel centres, origin at the top-left
pixel centre, +X right, +Y down (ADR-0007). Supply already display-oriented images at the exact
recorded resolution; do not silently crop, rescale or rotate them.

This JSON example supplies the board bootstrap and full manifest contract. Its nominal dimensions
are examples, not physical measurements. `board` can render while the verification flags are false;
`run` rejects an unverified board. Measure the print, replace the measured length/tolerance and set
the flags only after verification. A physically verified manifest with empty `views` and `references`
then produces `defer_missing_captures`, rather than a fitted calibration.

```json
{
  "schema_version": 1,
  "board": {
    "squares_x": 7,
    "squares_y": 5,
    "square_length_m": 0.04,
    "marker_length_m": 0.03,
    "dictionary": "DICT_5X5_100",
    "measured_square_length_m": 0.04,
    "square_length_tolerance_m": 0.0002,
    "flatness_verified": false,
    "print_scale_verified": false
  },
  "camera": {
    "device": "capture-device-id",
    "lens": "physical-lens-id",
    "width_px": 1920,
    "height_px": 1080,
    "orientation": "landscape",
    "zoom": 1.0,
    "focus": "locked",
    "stabilization": "off",
    "mode": "capture-mode-id"
  },
  "views": [],
  "references": [],
  "preregistration": {
    "frozen_before_capture": true,
    "protocol_id": "charuco-scale-v1",
    "minimum_relative_improvement": 0.2,
    "minimum_absolute_improvement": 0.005,
    "maximum_position_regression": 0.005
  }
}
```

Freeze the manifest protocol and thresholds before capture. Preserve that preregistration file
and hash separately from the later manifest containing capture paths/hashes. A boolean declaration
alone does not prove preregistration; retain the record showing when the capture protocol was frozen.

Add views as objects with `id`, `path`, `sha256`, `role` (`fit` or `held_out`), and `camera`
containing the exact camera object above. Held-out images must be separate captures, not repeated
paths, identical bytes, or fit-image crops. They are excluded from intrinsic fitting. Do not tune
the settings or gate thresholds after inspecting held-out results.

Add references with `id`, `path`, `sha256`, the same `camera`, `position` (`centre`, `left`,
`right`, `top`, `bottom`), `role` (`anchor` or `check`), `known_length_m`, `length_tolerance_m`,
`point_a_px`, `point_b_px`, `endpoint_tolerance_px`, and `plane_verified`. Endpoint arrays are
`[x_px, y_px]`. `endpoint_tolerance_px` is each endpoint's placement tolerance per coordinate;
`length_tolerance_m` describes the physical reference's measurement tolerance. Do not copy the
ChArUco square length as the independent reference length. The endpoint midpoint must match
the declared position: centre is the middle third on both axes; the other positions occupy the
corresponding edge third. Repeated check captures need distinct image hashes; the anchor image
must also be separate from all check images.

The `board` command accepts only the manifest's `board` object as its configuration, without
the surrounding camera/capture fields. Save that object as `board-config.json` before rendering.
This command renders nominal geometry without enforcing physical verification; it never marks
the print verified. The `run` command enforces that verification before fitting.

```powershell
python research/camera-calibration/charuco.py board --config validation/private/camera-calibration/board-config.json --output validation/private/camera-calibration/board.png
python research/camera-calibration/charuco.py run --manifest validation/private/camera-calibration/manifest.json --output validation/private/camera-calibration/report.json
```

Malformed input or a hash mismatch exits with status `2`. Valid evidence that fails a quality
gate is retained in a report and exits with status `0`; inspect the report decision and reasons,
not just the process exit status.

## Detection and calibration quality gates

Method version 1 freezes these preliminary research heuristics:

| Gate | Required support |
| --- | --- |
| Fit set | At least 10 accepted fit views |
| Per-view detections | At least 12 valid, distinct, noncollinear ChArUco corners |
| Per-view spatial support | Corner convex hull at least 1% of image area |
| Aggregate support | Corners span at least 60% of image width and height |
| Position diversity | Accepted-view centroids occupy at least 3 cells of a 3 x 3 grid |
| Pose diversity | At least 15 degrees of fitted board-tilt range |
| Conditioning | Column-scaled homography-constraint SVD: second-smallest / largest singular value at least `1e-5` |
| Fit reprojection | At most 2 px RMS |
| Held-out support | At least 3 accepted held-out board captures, at most 2 px RMS |

Retain inadequate views and rejection reasons; do not silently discard failure evidence or
automatically trim residual outliers to manufacture a passing fit. A successful solver return
is not sufficient: intrinsic/distortion/extrinsic values and reported uncertainties must be finite,
and the coverage/conditioning gates must pass. These gates have not been validated as a
production recording envelope.

The JSON retains original detected points/IDs, accepted and rejected views, `K`, distortion,
parameter standard deviations, extrinsics, global and per-view RMS, and point reprojection errors.
Low reprojection error alone does not prove physical scale accuracy or remove perspective/parallax.

## Independent corrected/uncorrected scale comparison

Use a separately measured rigid length, distinct from the fitting board, in the intended plate
face/sleeve plane. Keep the camera fixed and imaging settings unchanged. Verify coplanarity and
reference alignment for every measurement; a reference at a different depth cannot validate plate
scale. The filming rules in [SCALE_REFERENCE.md](../../docs/validation/SCALE_REFERENCE.md) apply.

Use exactly one central reference as the frozen scene-scale anchor. Gather at least two separate
check captures at each of the five positions, including the centre. Each position needs a horizontal
and a vertical check: dominant endpoint-delta axis classifies the direction. Record geometry in the
capture notes, and collect further independent repeats if needed to assess variation within either
direction. Neither the anchor nor these check references is an intrinsic fitting observation.

For each variant independently, let `d_anchor_px` be its anchor endpoint separation and set
`anchor_scale_m_per_px = known_anchor_length_m / d_anchor_px`. The raw variant uses the original
clicked coordinates; the corrected variant applies `undistortPoints(P=K)` to those same coordinates.
Estimate each check length as `anchor_scale_m_per_px * d_check_px`. Retain both variants, signed
and absolute fractional length errors, and local `known_length_m / d_check_px` scales, grouped by
position. Report scale range/spread across repeats and positions rather than collapsing it to one
apparently universal value.

Physical-length tolerances and endpoint perturbations accompany both anchor and check estimates.
For each endpoint the runner samples nine offsets with `dx,dy` in `{-tolerance,0,+tolerance}`,
including diagonals, and evaluates all 81 endpoint pairs.
Pass corrected perturbations through the same undistortion mapping. If sampled pixel separation
ranges from `d_low` to `d_high`, the anchor scale range is
`[(L - tolerance_m)/d_high, (L + tolerance_m)/d_low]`. The check/known-length ratio range is
`[d_check_low * scale_low / (L_check + tolerance_m), d_check_high * scale_high / (L_check - tolerance_m)]`.
Absolute-error lower bound is zero if this ratio range contains one; otherwise it is the smaller
endpoint error. The upper value is the larger endpoint error.

These are sampled sensitivity intervals, not statistical confidence intervals, rigorous extrema
over the continuous endpoint region, or complete calibration uncertainty. They do not account for
every camera, depth, board-flatness or solver-model error. Parameter standard deviations and repeat
variation remain separate diagnostics; do not silently convert them into calibrated probability or
tracking accuracy.

## Decision and evidence retention

The useful-improvement gate is frozen in the preregistration:

- Held-out independent-check median absolute fractional length error improves by at least 20%
  relatively and `0.005` absolutely (0.5 percentage points).
- Each position's median absolute error worsens by at most `0.005`.
- `median(raw sampled absolute-error lower values) - median(corrected sampled absolute-error upper
  values)` must be positive. The report calls this `conservative_improvement`; its conservatism is
  limited to the sampled sensitivity model above. Nominal improvement within that sensitivity is
  inconclusive.

Unverified physical board/reference inputs fail closed. With verified inputs, absent adequate board
captures, held-out support or independent-reference positions/directions yields `defer_missing_captures`
and the explicit missing list. Present evidence that fails calibration, held-out quality or usefulness
is retained as `defer_calibration_rejected`, `defer_held_out_quality` or `defer_no_useful_improvement`.
Passing evidence yields `use_for_tested_setup`.
A research use decision applies only to the tested camera settings, plane and capture conditions.
It is not permission to change canonical measurements or recommend universal distortion correction.
For each different lens, zoom, focus, resolution or stabilization condition, preregister and capture
a separate fit and independent check set. Compare each report's signed errors, error sensitivity and
local-scale ranges by position and direction; do not pool conditions into one profile. Matching
metadata still requires independent repeated captures to check imaging geometry stayed fixed.

Retain the preregistration, manifests/configs, nominal board PNG, physical measurement notes,
original images, all hashes, detector/solver options, full diagnostics, endpoint placements and
repeat measurements, output report and a byte-identical replay. Summarize the use/defer decision
with its evidence or exact missing captures. No measured usefulness result is claimed at present.

Production promotion is a separate owner decision requiring an ADR/contract proposal, recording
support evidence and an explicit integration plan. Intrinsics, scene scale and tracker accuracy
remain separate throughout. Existing
[plate calibration](../../docs/validation/PLATE_CALIBRATION.md) and
[canonical raw-data/provenance rules](../../docs/adr/0003-canonical-analysis-domain-model.md)
remain authoritative.

## Verification

```powershell
python -m unittest discover -v -s research/camera-calibration/tests -p 'test_*.py'
python research/camera-calibration/charuco.py --help
python validation/tools/schema_check.py
```

Tests use synthetic/local temporary inputs and the pinned research environment. No real-camera
calibration, physical scale accuracy or production recording envelope is established by them.
