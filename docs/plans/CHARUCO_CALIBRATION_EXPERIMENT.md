# ChArUco calibration experiment implementation plan

Issue: #177. Status: owner-approved bounded research design; physical evidence pending.

> For agentic workers: execute the ordered packages using the repository's existing execution
> workflow. This plan authorizes the research experiment; evaluation and production promotion
> remain separate decisions.

**Goal:** Build a reproducible local ChArUco intrinsic-calibration and independent scale-sensitivity
experiment, retaining either a supported use/defer decision or the explicit missing-capture list.

**Architecture:** One Python research script consumes hashed still images and a strict version 1
manifest. Intrinsic fitting, held-out board diagnostics and independent scene-length comparisons
remain distinct. Original coordinates and measurements are retained beside corrected values.

**Tech stack:** Existing `opencv-contrib-python==4.12.0.88`, `numpy==2.2.6`, Python standard library.

**Spec and acquisition protocol:**
[research/camera-calibration/README.md](../../research/camera-calibration/README.md).

## 1. Context and current state

M0 currently uses scalar plate-diameter calibration, not intrinsic/extrinsic camera calibration.
[PLATE_CALIBRATION.md](../validation/PLATE_CALIBRATION.md) identifies lens distortion,
perspective, camera motion and zoom as geometry limitations.
[SCALE_REFERENCE.md](../validation/SCALE_REFERENCE.md) provides a separate coplanar physical-length
check without rewriting calibration. The earlier
[VBT reference findings](../analysis/VBT_REFERENCE_EXPERIMENTS_FINDINGS.md) deferred scale-estimator
claims because independent physical ground truth was missing.

No real ChArUco calibration and independent-reference capture set is currently available. The
implementation must support a truthful `defer_missing_captures` report; synthetic recovery is not
measured evidence of usefulness.

## 2. Goal and explicit non-goals

Implement a board renderer and hashed-image experiment, reject inadequate/degenerate evidence,
retain full intrinsic diagnostics, and compare raw/corrected independent length measurements
under predeclared gates. Quantify local scene-scale variation and declared measurement sensitivity.

Non-goals: canonical/Rust/schema changes, #79, production undistortion, tracker tuning, a new
calibration method, video decoder, automatic camera acquisition, UI, cloud, live camera, or a
validated recording envelope. No dependency file, production crate or generic experiment framework
is needed.

## 3. Governing architecture and measurement invariants

- Preserve original images/detections/endpoints beside derived coordinates; never overwrite raw.
- Rejected/missing observations carry explicit reasons; no fabricated points or gap interpolation.
- Still-image identity is authoritative. If frames are extracted externally, retain source hashes,
  decoded timestamps and extraction settings; do not substitute nominal FPS for media timestamps.
- Use ADR-0007 display-oriented integer pixel centres, +X right and +Y down; explicitly preserve
  pixel-space output by passing `P=K` to `undistortPoints`.
- Reject nonfinite numeric inputs/results and inconsistent dimensions/settings. Confidence and
  parameter deviations are not calibrated cross-algorithm probabilities.
- Serialize stable sorted JSON without wall-clock times/random IDs; retain implementation/environment,
  board/input/source hashes, detector and solver options. Same pinned environment/inputs must replay.
- Rust `Analysis` remains authoritative; research outputs never enter canonical calibration silently.
- Apply clean-room and licence rules; no proprietary code/assets or unlicensed/private capture commits.

## 4. Dependencies and prerequisites

Reuse `research/opencv-tracking/requirements.txt`, OpenCV 4.12 ChArUco APIs and existing strict
JSON/hash conventions. Board construction/detection/matching and extended calibration APIs are
documented in the [official OpenCV 4.12 tutorial](https://docs.opencv.org/4.12.0/da/d13/tutorial_aruco_calibration.html).
No canonical media decode is required: inputs are already display-oriented still images.

Physical evaluation requires measured flat board dimensions, unchanged recorded camera configuration,
adequate distinct fit views, separate held-out board captures, and independent rigid-reference anchor
and repeated checks in the plate plane. Freeze acquisition/quality/usefulness rules before capture.

## 5. Ordered work packages

### Package 1: Contracts and reproducible inputs

Files: `research/camera-calibration/charuco.py`, `tests/test_charuco.py`.

- [ ] Test strict version/field validation, numeric boundaries, duplicate IDs/hash overlap,
  relative-path binding, actual-byte hashes and camera/resolution mismatch.
- [ ] Implement the manifest with `board`, `camera`, `views`, `references`, `preregistration` and
  required `schema_version: 1`. Physical board verification and camera reuse metadata are mandatory.
- [ ] Keep fit/held-out assignments explicit and freeze the preregistration independently.
- [ ] Reject unverified physical board/reference inputs and test/report missing captures after physical
  verification without inventing measurements.

Deliverable: malformed/unverified input fails closed; valid incomplete capture evidence produces the
missing list.

### Package 2: Board and calibration logic

Same script/test files; interface: `board --config CONFIG --output PNG` (board-only JSON) and
`run --manifest MANIFEST --output REPORT` (full manifest).

- [ ] Test board rendering and nominal geometry before physical verification; `board` permits false
  verification flags, while `run` requires measured/verified geometry before fitting.
- [ ] Detect original corners/IDs, match object/image points and reject invalid or inadequate views.
- [ ] Require at least 10 accepted fit views, 12 noncollinear distinct corners per view, hull area
  at least 1%, aggregate axis spans at least 60%, centroids in at least three 3 x 3 grid cells,
  fitted tilt range at least 15 degrees, and column-scaled intrinsic-constraint SVD second-smallest /
  largest singular-value ratio at least `1e-5`.
- [ ] Fit standard five-coefficient distortion using flags `0`, termination 100 iterations /
  `1e-9`, OpenCV threads `1`, RNG seed `0`; check finite/valid fitted values.
- [ ] Retain K/distortion, uncertainties, extrinsics, original detections, per-point residuals,
  global/per-view RMS and every rejection reason. Require fit RMS at most 2 px.
- [ ] Test synthetic known-camera recovery and deliberately degenerate/near-duplicate poses.

Deliverable: reproducible diagnostics distinguish solver completion from acceptable evidence.
These numerical gates are preliminary research heuristics, not validated production limits.

### Package 3: Held-out and independent scale evaluation

Same script/test files; consumes the frozen fit/config and original reference coordinates.

- [ ] Exclude held-out captures from fitting and refuse fit/held-out IDs or image-hash overlap.
  Require at least three accepted held-out board views and held-out RMS at most 2 px.
- [ ] Transform reference endpoints with `undistortPoints(P=K)` while retaining original coordinates.
  Test zero-distortion identity and a known synthetic correction.
- [ ] Compute independent raw/corrected central anchor scales; estimate separate check lengths and
  retain signed/absolute fractional errors and local `m_per_px` by image position.
- [ ] Require at least two distinct-hash check captures per centre/left/right/top/bottom position,
  covering both dominant-axis horizontal and vertical endpoint directions, with physically independent
  geometry and verified coplanarity. Check midpoint position against middle/edge image thirds,
  reject anchor/check hash overlap, and test absent positions/directions/repetitions.
- [ ] Evaluate nine endpoint offsets each (`dx,dy` in `{-tolerance,0,+tolerance}`), all 81 pairs, and
  physical-length tolerance through each variant. Use anchor reciprocal scale bounds and check ratio
  bounds; derive absolute-error intervals. Record that nonlinear continuous extrema are not proved
  and keep sampled sensitivity distinct from statistical confidence intervals.
- [ ] Test a useful improvement, position regression, uncertainty-limited improvement and absent data.

Deliverable: separated intrinsic, physical-scale and tracking conclusions with no canonical writes.
Malformed/hash-mismatched input exits `2`; valid quality/defer evidence is reported with exit `0`.

### Package 4: Evidence, tests and documentation

- [ ] Exercise the complete synthetic CLI path and compare repeated report bytes.
- [ ] Verify environment/source/requirements/board/input/options provenance and nonfinite rejection.
- [ ] Run targeted research tests, CLI help and the existing JSON schema catalogue check.
- [ ] Update the research README and this plan to match the implemented interface and uncertainty
  calculations. Do not add a committed report fixture/schema or regenerate canonical goldens.

## 6. Likely files and modules affected

Create only `research/camera-calibration/charuco.py`, `research/camera-calibration/tests/test_charuco.py`,
`research/camera-calibration/README.md`, and this plan. Reuse existing requirements as-is. Generated
images/reports and capture material belong under ignored `validation/private/camera-calibration/`
or synthetic `target/` directories. Canonical modules, schemas and workflow consumers are untouched.

## 7. Deterministic tests and verification commands

```powershell
python -m unittest discover -v -s research/camera-calibration/tests -p 'test_*.py'
python research/camera-calibration/charuco.py --help
python validation/tools/schema_check.py
git diff --check
```

Use the pinned research interpreter. Test strict manifest/input binding, board/detection edges,
nonfinite handling, degenerate coverage/conditioning, held-out isolation, synthetic fit/correction,
scene-scale comparison, bounded sensitivity assumptions, decisions, missing captures and byte replay.
Full Rust gates are required if scope later reaches Rust/canonical behavior; this research-only
change does not establish new Rust behavior or require physical-media fixtures.

## 8. Evidence artifacts to retain

Retain the frozen preregistration and hash, manifest/config, board PNG/hash, measured board dimensions
and tolerances/verification notes, camera settings, original capture files/hashes, rejection evidence,
full fit/held-out diagnostics, reference endpoint measurements/repeats, sensitivity model/results,
environment/source/options provenance, final report and byte-identical replay. Preserve source media
and decoded timestamps/settings if images came from video. No report instance or private media is
committed.

## 9. Decision and promotion gates

Separate three decisions:

1. **Implement experiment:** tests/provenance/contracts pass. Synthetic results support software
   behavior only.
2. **Evaluate evidence:** adequate physical/camera/fit/held-out/reference support must pass first.
   Useful correction requires median absolute fractional error improvement of at least 20% relatively
   and `0.005` absolutely, no position median regression beyond `0.005`, and positive median raw lower
   minus median corrected upper sampled-error values (`conservative_improvement`). This latter gate
   is conservative within the sampled sensitivity model, not a rigorous continuous-region bound.
   Missing capture support yields `defer_missing_captures` with an exact list; unverified physical
   inputs fail closed, and poor quality/inconclusive benefit yields a reasoned defer. Never tune gates
   after held-out inspection.
3. **Promote to production:** separate owner approval and ADR/contract/integration work. A research use
   decision applies only to tested settings, plane and conditions and does not authorize #79 or M0 changes.

The threshold fractions are 0.5 percentage points where `0.005` is specified. They are frozen research
criteria, not measured OpenBar recording-support guarantees.

## 10. Stop conditions

Stop evidence interpretation when board physical verification, matching camera geometry, sufficient
coverage/pose diversity/conditioning, independent geometry or held-out support is missing. Retain the
failures and requested captures. Stop implementation expansion at any canonical/Rust/schema or #79
boundary. Do not replace missing captures with synthetic physical-accuracy claims or reuse an intrinsic
fit across changed camera modes/settings without re-evaluation.

## 11. Rollout and documentation updates

Ship the isolated script/tests and acquisition protocol. Record the initial state as physical evidence
pending, then append an evidence-backed use/defer record after capture without changing preregistration.
If promoted separately, propose an ADR describing intrinsic correction versus scene calibration,
recording-envelope reuse evidence, coordinate/derived-data provenance and failure handling; identify
affected `PLATE_CALIBRATION`, canonical schema/version/golden, pipeline/render and recording-support
contracts before implementation. This experiment does not perform that promotion.
