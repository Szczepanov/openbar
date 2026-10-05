# Bar-path measurement research

Implements the development portions of
[the PR #97 experiment plan](../../docs/plans/M0_BAR_PATH_MEASUREMENT_EXPERIMENT_PLAN.md).
This layer supplies diagnostics and baseline inventory. Image measurements and fusion follow in separate layers.

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
