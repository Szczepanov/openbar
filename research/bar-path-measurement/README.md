# Bar-path measurement research

Implements the development portions of
[the PR #97 experiment plan](../../docs/plans/M0_BAR_PATH_MEASUREMENT_EXPERIMENT_PLAN.md).
This is an isolated experiment, not a production tracker. See
[the development decision](../../docs/analysis/M0_BAR_PATH_MEASUREMENT_DEVELOPMENT_RESULTS.md).

## Environment and boundaries

The `snapshot` and `diagnose` commands use the standard library only. The surrounding bar-path
research stack reuses the existing pinned OpenCV environment from
`research/opencv-tracking/requirements.txt` rather than introducing a second dependency file. It pins
OpenCV 4.12.0 and NumPy 2.2.6. OpenCV packaging is MIT, bundled OpenCV is Apache-2.0, NumPy is
BSD-3-Clause; wheel notices remain applicable. Sources: [OpenCV packaging](https://github.com/opencv/opencv-python),
[NumPy](https://numpy.org/). No new dependency enters the Rust workspace or stdlib validation tools.

For repeatability, set `OPENBLAS_NUM_THREADS=1`, `OMP_NUM_THREADS=1`, and `MKL_NUM_THREADS=1`
before starting the interpreter when using the OpenCV environment. The image-measurement runner sets
OpenCV threads to one. FFmpeg/ffprobe must be on PATH for canonical media analysis and `run`; the
diagnostics and baseline inventory commands themselves do not decode media.

Every command accepts only development fixtures. There is deliberately no `--allow-held-out`:
candidate freeze and final selection remain governed by #57. This runner does not select candidates,
revise the 3 px or 99% gates, or establish physical velocity accuracy. A baseline snapshot is not
a candidate freeze. Private fixture outputs must stay in this worktree's `validation/private/`;
`snapshot` enforces the same destination rule when a supplied input identifies a private fixture.
Never commit private predictions, sidecars, labels, extracted frames or media.

## Automatic replay without new manual work

`batch_report.py` runs the existing diagnostics for several retained `baseline.json` snapshots:

```powershell
python research/bar-path-measurement/batch_report.py --manifest validation/private/manifest.json --snapshot CLIP=validation/private/bar-path-measurement/CLIP/baseline.json --repository-root . --output-dir validation/private/automatic-development-report --canonical
```

Repeat `--snapshot` for each development clip. The snapshots must retain valid manifest and
annotation/seed/SAM/CSRT input hashes. All inputs are checked before reports are written; held-out
fixtures, duplicate IDs and existing output directories are refused. No tracker or model is rerun.
The stdlib-only default writes a batch README/summary and existing per-clip motion diagnostics.
Optional `--canonical` runs the authoritative Rust benchmark/analyze commands and renders separate
`csrt.svg`/`sam.svg` path/position/velocity/confidence diagnostics. It requires Cargo and FFmpeg.

This reuses existing confirmed seeds and sparse labels; no new human annotation, pseudo-labels or
consensus coordinates are generated. Results remain development evidence. Sparse labelled intervals
do not establish adjacent-frame accuracy, stationary jitter or annotation repeatability, and rendered
velocity is not independently validated physical accuracy. The raw filter and 0.2 s maximum gap
are explicit experiment settings, not a selected production pipeline.
The batch table excludes initialization; the existing canonical benchmark output includes it and
therefore has different label counts/averages. Use the seed-excluded diagnostics for this comparison.

## Baseline and motion diagnostics

The bounded [controlled-motion experiment](../../docs/plans/CONTROLLED_MOTION_EXPERIMENT_PLAN.md)
replays three existing confirmed development seed rasters with known whole-frame translations,
fixed blur and brief occlusion. `controlled_motion.py` supplies the non-wrapped transform and
seed-relative displacement/support/confidence/recovery diagnostics; its tests run in the same
research suite below. The producer orchestration and all generated inputs remain private.
See the [aggregate result](../../docs/analysis/CONTROLLED_MOTION_EXPERIMENT_RESULTS.md).
An [exact replication on current main](../../docs/analysis/CONTROLLED_MOTION_REPLICATION_RESULTS.md)
retains the same failure evidence without introducing another configuration.
Injected displacement is not an annotation or independent physical reference. Whole-frame
motion also moves the background; no production tracker/filter is selected by this experiment.

The subsequent [CSRT trace and paired background result](../../docs/analysis/CSRT_BACKGROUND_MOTION_RESULTS.md)
uses `box_diagnostics.py` to decompose returned-box residuals and `background_motion.py` to
prepare a fixed pixel cutout/background pair. Both preserve the original producer and scorer.
The cutout boundary and cleared corridor are artificial; the paired result is synthetic
development evidence, not natural-scene or physical velocity validation. Plans, private
orchestration and stop conditions are linked from the result.

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

## Image experiments

The coarse prediction is a read-only input. Use an existing CSRT or SAM output, with its full
implementation/configuration provenance. Refined positions are never fed back to the coarse tracker.
The seed radius remains fixed. Frames are decoded through the existing OpenCV research decoder;
timestamps and display rotation come from `label_package.probe`, not nominal FPS or VideoCapture.
Decoded/probed counts and the FFmpeg exit status are checked even after the selected range ends;
decoder contract failures are surfaced as a normal fail-closed command error rather than leaking a
research helper traceback.

```bash
python research/bar-path-measurement/experiment.py run \
  --manifest validation/private/manifest.json --fixture dev \
  --seed validation/private/seeds/dev.manual-target-seed-v1.json \
  --coarse validation/private/study/csrt.prediction-v1.json \
  --method radial --output-dir validation/private/study/radial
```

`--repository-root` optionally points at an existing owner checkout containing manifest-relative
media. Media hashes and raster/rotation metadata must match. This does not copy private data into
the repository. Coarse timestamps must exactly identify probed frames rounded to six decimal places;
timestamp collisions are refused. Seed frame index, when present, must agree with its decoded time.

The registration tests cover both ideal Fourier/circular shifts and finite non-periodic crops whose
newly exposed pixels come from outside the first crop. The latter is important because the local DFT
is only the proposal step; real video ROIs do not wrap around at their borders. Default-window
regressions also include repeated texture with multiple real-overlap translations inside the motion
gate; that case must fail closed rather than emit the Hann-window-preferred peak.

The output contains absolute and fused `tracker-prediction-v1` streams, separate raw/diagnostic
sidecars, and separate desktop timing. Prediction/sidecar bytes exclude runtime. Provenance retains
git commit/dirty state, source/helper/input hashes, full configurations, dependency and decoder
versions, and thread settings. Timing includes selected measurement computation and full decoder
drain, but excludes the already computed coarse producer. It cannot satisfy the Pixel 8 full-path
runtime gate.

The manual seed is emitted with tracker confidence `1.0`, matching its role as the deterministic
initialization observation. Optional `selection_confidence` is human/annotation confidence, not
tracker confidence: it is retained separately in diagnostics/provenance and is used only as the
optional fusion seed-anchor weight. This preserves the two confidence domains explicitly.

`--config FILE` accepts only `absolute`, `registration`, `fusion` and `patch_radius_factor`. Each
subobject can override known settings below. Unknown, non-finite or out-of-range settings fail closed.
Do not change configurations after examining held-out evidence.

| Component | Defaults and units |
| --- | --- |
| Radial circle | 72 rays; radius band +/-30%; step 0.5 px; gradient >=8 intensity/px; contrast >=20 intensity; radius 0.8..1.2 of seed; centre offset <=0.25 seed radius; inlier tolerance 1.5 px; fit RMS <=0.9 px; inlier/ray support >=0.6; angular support >=0.65 |
| Marker | OpenCV HSV [35,80,60]..[85,255,255]; ROI radius 2.0 seed radii; fitted radius 0.75..1.25 of seed; centre offset <=0.6 seed radius; area >=0.65 expected disk; circularity >=0.75; purity >=0.85; angular support >=0.8; contour inliers >=0.9; fit RMS <=1.2 px; inlier tolerance 1.5 px |
| Registration | local DFT upsampling 50; deterministic real-overlap refinement at 0.5/0.2/0.08/0.03 px scales; shift norm <=12 px in crop coordinates; intensity SD >=2; forward/reverse phase peak ratio >=1.5; up to 32 strongest non-main-lobe integer phase proposals rescored on real overlap; aligned correlation >=0.9; overlap >=0.65; forward/backward inconsistency <=0.08 px; Hann window enabled |
| Fusion | absolute/relative base weight 1 each; Huber distance 2 px; eight fixed IRLS iterations; max gap 0.2 s; absolute confidence floor 0.000001 for numerical anchoring only; relative confidence is used directly, so zero-confidence relative evidence has zero solve weight |
| Runner patch | half-width = ceil(1.5 seed radii), resulting odd side length; allowed factor 1.25..3; integer crop origins added back to local relative shifts; incomplete patches rejected |

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

Fusion minimizes robust absolute residuals and measured relative displacement residuals with no
smoothness prior. This conservative version requires valid absolute evidence at every emitted frame.
Absolute loss, missing relative edges and excessive gaps split solves. Relative-only chains never
fill lost positions. Confidence is capped by absolute evidence; absolute disagreement reduces it
directly, while relative-disagreement penalties are scaled by the corresponding registration
confidence. A zero-confidence relative edge therefore neither moves the solution nor vetoes fused
confidence. The absolute numerical floor does not promote certainty. Raw absolute/relative
observations remain intact.

## Controlled-marker capture protocol

The default targets a filled, high-contrast green circle; other colours require recorded HSV bounds
(wrapping hue bounds are supported). Use `--method marker` with a human-confirmed marker centre and
marker radius seed. Keep the natural plate visible in the same recording where possible.

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

## Remaining evidence and stop conditions

Do not freeze a candidate that achieves lower error by losing difficult frames. Examine represented
lifts, availability, false tracks, paired support, drift, confidence and downstream physical results.
Only useful development evidence justifies extending the existing
[tracker/filter selection](../../docs/validation/TRACKER_FILTER_SELECTION.md) freeze mechanism to this
research producer. Preserve its manifest/commit/configuration checks, repeatability prerequisites and
one-shot held-out rule. This runner's development refusal is intentional until those prerequisites pass.

YOLO26 remains conditional on measured coarse-localizer failure, with separate licensing review.
No learned detector/model/dependency was added. Current geometry failures do not demonstrate that need.

Independent synchronized reference capture remains pending. Use the existing
[reference-study protocol and command](../../docs/validation/M0_REFERENCE_STUDY.md) when data exists.
Freeze axes, intervals, calibration, filtering, definitions, synchronization and uncertainties before
comparison. WL Analysis or same-video marker agreement cannot close the physical accuracy gates.

Pixel 8 measurement remains pending. Use the existing
[phone runtime protocol](../../docs/validation/PHONE_RUNTIME_BENCHMARK.md) after candidate/envelope freeze.
Any future research pipeline must time its complete producer path, not just `analyze --observations`.
Current desktop sidecars do not prove deployability or speed on the phone.

## Frozen plate-scale diagnostic (#88)

`plate_scale_study.py` is a stdlib-only diagnostic using retained SAM bplus-circle geometry
and existing confirmed stick references. The [frozen plan](../../docs/plans/PLATE_SCALE_STUDY_PLAN.md)
defines the eight development cases, eligibility, median-radius estimator, reciprocal empirical
p10–p90 band and decision rule. Initialization, rejected fits and loss cannot supply radius evidence.
Insufficient support emits no recommended scale or band. The band describes apparent-size spread;
it is not calibrated physical-scale uncertainty and must not be attached to velocity as such.

Run `freeze` before scoring (paths below refer to the owner's existing checkout and this worktree):

```powershell
python research/bar-path-measurement/plate_scale_study.py freeze --repository-root C:/Users/mdszc/Downloads/projekty/openbar --manifest C:/Users/mdszc/Downloads/projekty/openbar/validation/private/vbt/manifest.json --session C:/Users/mdszc/Downloads/projekty/openbar/validation/private/vbt/sessions/2026-10-03/session-record.json --cli target/debug/openbar-cli.exe --output validation/private/plate-scale-study-2026-10-06/frozen-inputs.json
python research/bar-path-measurement/plate_scale_study.py score --bundle validation/private/plate-scale-study-2026-10-06/frozen-inputs.json --output validation/private/plate-scale-study-2026-10-06/report-a.json
```

The output parent must exist; existing outputs are refused. Freeze verifies original media hashes,
confirmed inputs, display geometry, source PTS, sidecar provenance and canonical analyses through
the Rust `render` consumer. It records input/source/plan hashes before any estimator scoring.
`score` replays the validated frozen evidence without a decoder, tracker or GPU run. Comparing two
reports establishes frozen-input CPU reproducibility; it says nothing about GPU repeatability.
Private outputs remain under git-ignored `validation/private`. Canonical calibration stays unchanged.

## Verification

```bash
python -W error -m unittest discover -v -s research/bar-path-measurement/tests -p 'test_*.py'
```

Use the pinned research interpreter. Tests cover numerical geometry/registration, loss, drift,
deterministic fusion, zero-confidence relative evidence, exact matching, dense-label jitter,
seed-confidence domain separation, accepted seed-frame mapping, decoder failure propagation, strict
existing contracts, private/development boundaries, crop-origin mapping and canonical CLI delegation.
The registration tests cover both ideal Fourier/circular shifts and finite non-periodic crops whose
newly exposed pixels come from outside the first crop; the local DFT is only the proposal step for
real video ROIs and must not rely on wrap-around. Synthetic tests establish implementation behavior,
not real-video accuracy.
