# Bar-path measurement research

Implements the development portions of
[the PR #97 experiment plan](../../docs/plans/M0_BAR_PATH_MEASUREMENT_EXPERIMENT_PLAN.md).
This stacked directory supplies diagnostics, baseline inventory, and the image-measurement primitives below. Fusion follows in the next layer.

## Environment and boundaries

The `snapshot` and `diagnose` commands in this layer use the standard library only. The surrounding
bar-path research stack reuses the existing pinned OpenCV environment from
`research/opencv-tracking/requirements.txt` rather than introducing a second dependency file. It pins
OpenCV 4.12.0 and NumPy 2.2.6. OpenCV packaging is MIT, bundled OpenCV is Apache-2.0, NumPy is
BSD-3-Clause; wheel notices remain applicable. Sources: [OpenCV packaging](https://github.com/opencv/opencv-python),
[NumPy](https://numpy.org/). No new dependency enters the Rust workspace or stdlib validation tools.

For repeatability, set `OPENBLAS_NUM_THREADS=1`, `OMP_NUM_THREADS=1`, and `MKL_NUM_THREADS=1`
before starting the interpreter when using the OpenCV environment. The image-measurement runners set
OpenCV threads to one. FFmpeg/ffprobe must be on PATH for canonical media analysis; the diagnostics and
baseline inventory commands themselves do not decode media.

Every command accepts only development fixtures. There is deliberately no `--allow-held-out`:
candidate freeze and final selection remain governed by #57. This runner does not select candidates,
revise the 3 px or 99% gates, or establish physical velocity accuracy. A baseline snapshot is not
a candidate freeze. Private fixture outputs must stay in this worktree's `validation/private/`;
`snapshot` enforces the same destination rule when a supplied input identifies a private fixture.
Never commit private predictions, sidecars, labels, extracted frames or media.

## Baseline and motion diagnostics

`snapshot` records the current commit and dirty state, implementation/helper hashes, manifest
hash, development/held-out fixture IDs, supplied input hashes and candidate/annotation provenance.
It refuses inputs that identify held-out fixtures and does not open held-out media. Supply repeatability
reports and existing canonical analysis/configuration evidence as named inputs when available; absent
evidence remains absent. An existing #57 freeze can be referenced with `--selection-freeze`, without
modifying it.

```bash
python research/bar-path-measurement/experiment.py snapshot \
  --manifest validation/private/manifest.json \
  --input csrt=validation/private/study/csrt.prediction-v1.json \
  --input annotation=validation/private/annotations/dev.annotation-v1.json \
  --output validation/private/study/baseline.json

python research/bar-path-measurement/experiment.py diagnose \
  --manifest validation/private/manifest.json --fixture dev \
  --seed validation/private/seeds/dev.manual-target-seed-v1.json \
  --annotation validation/private/annotations/dev.annotation-v1.json \
  --candidate csrt=validation/private/study/csrt.prediction-v1.json \
  --candidate candidate=validation/private/study/radial.prediction-v1.json \
  --relative candidate=validation/private/study/radial.sidecar.json \
  --baseline csrt --output-dir validation/private/study/comparison
```

Replace `dev` and paths with an actual development fixture and existing outputs. Candidate names
are report labels; `name=path` must be unique. JSON contracts, identities, source hashes, finite
values, increasing timestamps, display bounds and lost states are validated before scoring.

Reports retain all-sample and seed-excluded centre/axis errors, displacement errors, robust
constant-offset-adjusted errors, motion-linked correlation, unavailable intervals, confidence/error
bins, relative drift and paired comparisons on common support. Offsets never alter raw predictions.
The accepted seed is resolved once from the designated baseline to its actual frame; observations and
labels then match by **exact stored timestamp**, with no nearest-neighbour matching or interpolation.
Seed-excluded reports omit intervals touching that resolved seed and never join labels across it.
The same resolved timestamp is passed into optional canonical output generation, so candidate argument
ordering cannot change the selected range.

Delta intervals use consecutive annotation records, require both labelled endpoints, and reject any
intervening predicted loss or observation gap above `--max-gap-s` (default 0.2 s). Missing annotation
states break intervals. Reports include actual interval duration and annotation frame-index distance,
so sparse labels are never described as adjacent frames. Relative intervals require every intervening
measured edge; drift resets after loss or the seed-exclusion boundary and starts only from a labelled
anchor. Relative confidence bins use the minimum edge confidence in the interval, an algorithm-specific
diagnostic.

Stationary jitter is unsupported unless `--stationary-window START_S:END_S` is supplied, every
predicted timestamp has a label, the label frame indices are consecutive, there are at least three
frames, and each labelled axis spans at most 0.5 px. Reported jitter is the population SD of position
error against those dense labels. Sparse predictions alone cannot establish dense coverage.

`--canonical` additionally delegates centre benchmarks and calibrated/filter/kinematic calculations
to existing Rust `benchmark` and `analyze --observations` commands. The explicit development analysis
uses plate-diameter calibration, raw filtering, the requested maximum kinematic gap, and minimum
confidence zero. These settings are not production defaults or a filter selection. Python does not
reimplement ROM, mean velocity, peak velocity or M0 gate semantics.
Supply `--repository-root` when fixture media lives in another checkout. Canonical analysis receives
that verified media as an explicit `--video` override and selects from the resolved seed to the later
of the last label and that candidate's last observation, preserving unlabelled prediction tails.
One-microsecond endpoint padding accommodates stored six-decimal timestamps.

## Verification

```bash
python -W error -m unittest discover -v -s research/bar-path-measurement/tests -p 'test_*.py'
```

The registration tests cover both ideal Fourier/circular shifts and finite non-periodic crops whose
newly exposed pixels come from outside the first crop. The latter is important because the local DFT
is only the proposal step; real video ROIs do not wrap around at their borders. Default-window
regressions also include repeated texture with multiple real-overlap translations inside the motion
gate; that case must fail closed rather than emit the Hann-window-preferred peak.

## Image measurement primitives

The research-only `vision.py` exports `marker_center`, `radial_center`, and `relative_shift`.
Absolute functions accept a BGR image, coarse display centre, seed radius and configuration.
Relative registration accepts two same-size grayscale patches and configuration.
Success returns `center_px` or `delta_px`, confidence and diagnostics; loss carries null coordinates and confidence.
No coarse tracker state is updated. The final runner follows in the next layer.

| Component | Defaults and units |
| --- | --- |
| Radial circle | 72 rays; radius band +/-30%; step 0.5 px; gradient >=8 intensity/px; contrast >=20 intensity; radius 0.8..1.2 of seed; centre offset <=0.25 seed radius; inlier tolerance 1.5 px; fit RMS <=0.9 px; inlier/ray support >=0.6; angular support >=0.65 |
| Marker | OpenCV HSV [35,80,60]..[85,255,255]; ROI radius 2.0 seed radii; fitted radius 0.75..1.25 of seed; centre offset <=0.6 seed radius; area >=0.65 expected disk; circularity >=0.75; purity >=0.85; angular support >=0.8; contour inliers >=0.9; fit RMS <=1.2 px; inlier tolerance 1.5 px |
| Registration | local DFT upsampling 50; deterministic real-overlap refinement at 0.5/0.2/0.08/0.03 px scales; shift norm <=12 px in crop coordinates; intensity SD >=2; forward/reverse phase peak ratio >=1.5; up to 32 strongest non-main-lobe integer phase proposals rescored on real overlap; aligned correlation >=0.9; overlap >=0.65; forward/backward inconsistency <=0.08 px; Hann window enabled |

Radial rays sample raw luminance and refine gradient maxima to sub-pixel positions, followed by a
deterministic robust circle fit. No ellipse or radial-symmetry alternative is included in this first
experiment. A plausible circle does not establish physical plate identity among concentric rims.
Registration independently implements the local inverse-DFT principle from Guizar-Sicairos et al.,
2008, [DOI 10.1364/OL.33.000156](https://doi.org/10.1364/OL.33.000156); no reference code is copied.
The phase/local-DFT estimate is then refined by maximizing normalized correlation only over real
non-wrapped overlap. `overlap_fraction` counts valid pixel-centre samples, so an exact zero shift has
full overlap (`1.0`). Diagnostics retain the pre-refinement phase delta and refinement magnitude.

Hann windowing reduces finite-crop edge effects but can also suppress repeated-texture phase sidelobes,
so the windowed phase peak ratio is not treated as sufficient uniqueness evidence. The 32 strongest
integer phase alternatives outside the two-pixel main-lobe guard and inside the configured shift gate
are rescored on real, non-wrapped overlap. If a spatially distinct alternative passes the same
correlation and overlap thresholds as an accepted translation, the result is lost with
`ambiguous_real_overlap`; the best competing proposal remains in diagnostics. This is a bounded
ambiguity diagnostic, not proof that all aliasing is impossible, so quality must still be compared
with measured development error before promotion.

The final measurement uses a translation-only model and rejects unsupported rotation/appearance
through overlap correlation. Forward/backward consistency is an internal check, not independent
correctness evidence. Texture or background inside the ROI can still confound plate displacement;
quality must be compared with error on development evidence before promotion.

## Controlled-marker capture protocol

The default targets a filled, high-contrast green circle; other colours require recorded HSV bounds
(wrapping hue bounds are supported). Call `marker_center` with a human-confirmed marker centre and marker radius. Keep the natural plate visible in the same recording where possible.

Before capture, record marker physical diameter/material/colour, its alignment and centring tolerance
relative to the bar axis, camera/lens/exposure settings, natural plate visibility, and whether sleeve
rotation can move the marker off axis. Record static and fast-motion windows, blur/lighting failures,
and a fixed marker-to-plate pixel-scale ratio with uncertainty, since the planes can differ.

Keep the marker trajectory separate. For same-video comparison, account explicitly for fixed mounting
offset and the measured depth scale ratio; retain both original and aligned diagnostic trajectories.
Do not use the marker diameter as plate calibration by default or optimize alignment against desired
velocity agreement. Comparison/alignment cannot be validated until real marker clips exist.

The marker is a development aid, not independent physical truth or an athlete-facing requirement.
Current capture status: **pending**, because no such recordings were supplied.
