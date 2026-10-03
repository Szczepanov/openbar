# M0 Headless CLI Pipeline

Issue #12 makes `apps/openbar-cli` the integration surface for the validated M0 measurement
pipeline. The CLI orchestrates media and tracking, while authoritative measurement semantics remain
in `openbar-core`.

## Commands

### `analyze`

`analyze` executes:

```text
video
  -> ADR-0006 ffprobe/ffmpeg frame source
  -> explicitly selected manual-seed tracker
     OR validated external tracker-prediction-v1 observations
  -> raw observations, including explicit low-confidence/lost state
  -> plate-diameter calibration
  -> explicitly selected filter
  -> timestamp-authoritative backward-difference kinematics
  -> canonical analysis-v1 JSON
```

Example using the public synthetic fixture:

```bash
cargo run --locked -p openbar-cli -- analyze \
  --manifest validation/fixtures/public/manifest.json \
  --fixture synthetic-clean-side-12 \
  --seed validation/fixtures/public/seeds/synthetic-clean-side-12.manual-target-seed-v1.json \
  --plate-diameter-m 0.45 \
  --tracker template \
  --filter raw \
  --kinematics-max-gap-s 0.2 \
  --kinematics-min-confidence 0 \
  --output target/analyze-smoke.json
```


The same canonical pipeline can consume an already-produced `tracker-prediction-v1` stream.
Exactly one of `--tracker` and `--observations` is required:

```bash
cargo run --locked -p openbar-cli -- analyze \
  --manifest validation/fixtures/public/manifest.json \
  --fixture synthetic-clean-side-12 \
  --seed validation/fixtures/public/seeds/synthetic-clean-side-12.manual-target-seed-v1.json \
  --plate-diameter-m 0.45 \
  --observations validation/fixtures/public/predictions/synthetic-perfect.prediction-v1.json \
  --filter raw \
  --kinematics-max-gap-s 0.2 \
  --kinematics-min-confidence 0 \
  --output target/analyze-external-smoke.json
```

`--observations` is an ingestion boundary, not a second measurement implementation. The CLI
validates and adapts the stream, then the existing `openbar-core` calibration, filtering and
kinematics code produces the derived layers. The CLI does not invoke Python/OpenCV or add a CV/ML
runtime dependency.

Before any analysis or recording-support output is written, an external prediction must satisfy
all of the following:

- `schema_version` is 1 and `coordinate_space` is `decoded_display_pixels`;
- `source_video_sha256` is present and matches the media OpenBar probed;
- in fixture mode, `fixture_id` matches the selected fixture;
- samples are strictly timestamp-increasing and every timestamp matches a selected decoded frame
  within the same 0.5 ms tolerance used for the manual seed; each sample must resolve to a distinct,
  strictly advancing decoded frame;
- the first sample matches the manual-seed timestamp;
- tracked centres are finite and inside the ADR-0007 display window, with finite confidence in
  `[0, 1]`;
- lost samples carry neither a centre nor confidence;
- the external implementation name already satisfies the canonical tracker identifier grammar.
  OpenBar does not rewrite names such as `opencv-csrt+lk` into a different identity.

Tracked coordinates, timestamps and confidence values are preserved as canonical raw
observations. Lost samples remain lost and contribute no position; import never interpolates or
revives them. The matched decoded frame index is attached to each canonical raw observation. If a
prediction timestamp represents the first or last selected decoded frame but differs from that
frame's exact probe timestamp only by the accepted decoder tolerance (for example a fixture timestamp
rounded to six decimals), the external-analysis `video.trim` envelope is widened only enough to
contain the preserved imported boundary timestamp. The built-in `--tracker` path keeps its existing
exact decoded-media trim bytes unchanged.

External provenance uses the existing `analysis-v1` tracker provenance shape. The prediction
implementation name becomes the tracker id/implementation, its version is preserved, and each
config entry is retained in `provenance.tracker.implementation.parameters`. Scalar config values
use the native canonical parameter type when exactly representable; unsigned integers above the
canonical i64 range, plus object/array/null values, are retained as deterministic compact text rather
than rounded through a lossy floating-point conversion. OpenBar also records
`prediction_sha256`, the SHA-256 of the exact prediction-file bytes. Tracker runtime is validated
when present but intentionally does not enter canonical analysis output, so wall-clock timing
cannot make repeated analysis non-deterministic.

A direct media path may be supplied with `--video`. In fixture mode, `--video` is an explicit
media override and the decoded source is still checked against the fixture's hash/encoded
dimensions/rotation when those values are present. Fixture mode also cross-checks
`--plate-diameter-m` against the manifest's required `load.plate_diameter_m` before media I/O,
so a fixture identity cannot silently carry a different metric scale.

After media decode, the manual seed is validated against the decoded display geometry/timeline
before any recording-support artifact is derived from it. Recording support is then evaluated
before tracking/calibration/kinematics and before physical analysis is emitted. Fixture mode reuses
the camera/condition metadata from the #3 manifest. Direct-video mode must provide
`--camera-view` and `--camera-movement`; the CLI does not assume side/fixed geometry.

Optional direct-video metadata is accepted through `--approx-yaw-deg`,
`--approx-pitch-deg`, `--camera-roll-deg`, and `--camera-distance-m`.
These values are recorded for support assessment but are not compared with invented cutoffs.
The support document's `measured_fps` is derived from decoded presentation timestamps; fixture
`nominal_fps` remains authored manifest metadata.

`--recording-support-output <path>` writes the versioned recording-support-v1 assessment.
Declared side + fixed camera is currently warning-only because #14 has no held-out
non-synthetic accuracy evidence. `oblique_45`, front, rear, unknown view, and
handheld/panning/moving/unknown camera stability are rejected with exit code 4 before canonical
analysis is written. See [RECORDING_ENVELOPE.md](RECORDING_ENVELOPE.md).

There is deliberately **no default observation source or production filter**. The caller must
choose either an internal tracker family with `--tracker` or a validated external prediction with
`--observations`, and must also choose the filter. Filtering evidence from #10 explicitly deferred
a production winner; the CLI therefore cannot silently promote a candidate. Filter-specific
parameters are required where applicable and irrelevant parameters are rejected. Internal tracker
family defaults may be used after the family itself is selected, and the complete effective
tracker configuration is persisted in analysis provenance. Tracker-specific CLI options are
rejected when `--observations` is selected because the imported stream already owns its tracker
configuration.

The kinematics continuity threshold and minimum-confidence threshold are also required explicitly.

#### Smoothing windows in samples or seconds

For `moving-average` and `savitzky-golay`, provide exactly one of
`--filter-window <odd sample count>` and `--filter-window-s <positive seconds>`.
`--filter-max-gap-s` remains required; Savitzky–Golay also requires
`--filter-polynomial-order`. `raw` and `kalman` reject both smoothing-window flags.

For example, `--filter savitzky-golay --filter-window-s 0.15
--filter-polynomial-order 2 --filter-max-gap-s 0.2` resolves to 9 samples at 60 fps
and 5 samples at 30 fps. There is still no default filter or default window.

Seconds are resolved by core `resolve_window_samples`, after seed validation and before
tracking. The rate is `video.frame_rate.measured_fps`: `(selected_frame_count - 1) /
(last_selected_timestamp_s - first_selected_timestamp_s)`, from decoded presentation
timestamps over the selection, never nominal FPS or the count of tracked observations.
At least two selected frames with increasing timestamps are required. Non-finite or
non-positive durations/rates and unrepresentable sample counts fail with invalid input
(exit 2); ordinary parameter validation still happens before media I/O.

With `requested = window_s * measured_fps`, nearest-odd rounding is
`2 * floor((requested - 1) / 2 + 0.5 + 1e-9) + 1`. Ties round up: 0.1 s at
60 fps resolves from 6 to 7 samples. Clamp to at least 1 for moving average, or the
smallest odd count greater than the polynomial order for Savitzky–Golay. Reject if
the resolved count differs from the request by more than **1 sample**
(`DURATION_WINDOW_TOLERANCE_SAMPLES`), with only a `2e-9`-sample numerical cushion
matching the tie-rounding epsilon. Thus 0.05 s at 30 fps with order 3 fails:
1.5 requested samples would require a 5-sample window.

Filter provenance records both `window_s` (requested seconds) and `window` (resolved
sample count). Fixed sample-count requests omit `window_s` and retain their existing
output bytes. `measured_fps` stays in video metadata; it is not duplicated in filter
parameters. No schema or filter implementation version changes are needed: filter
maths and the existing free-form parameter map are unchanged.

On variable-frame-rate clips this mean-rate conversion gives a fixed sample window
whose actual duration varies with local timestamps. It does not create an exact
time-based variable window. Existing timestamp-aware fitting, segment boundaries,
gap handling, raw observations and confidence semantics remain unchanged.

### `benchmark`

`benchmark` continues to invoke the common #5 benchmark harness. The versioned JSON artifact is
written to `--output` when supplied and the concise human-readable report is written to stderr.

### `render`

`render --analysis <analysis.json> --output <report.svg>` consumes validated canonical
analysis-v1 and produces a deterministic diagnostic SVG. Optional
`--video <source-video>` embeds a verified display-oriented source frame; optional
`--frame-timestamp-s <s>` must identify a canonical raw-observation timestamp. Existing reports
are not overwritten unless `--force` is supplied.

The renderer does not rerun tracking, filtering, calibration, interpolation, or kinematics.
Lost observations break plotted paths, low-confidence observations remain explicit, and absent
filtered/kinematic layers are reported as absent. See `DIAGNOSTIC_RENDERING.md` for the rendering
and source-frame verification contract.

## Canonical analysis guarantees

`analyze` constructs the existing `openbar_core::analysis::Analysis`; it does not define a
parallel persistence model.

The output therefore preserves:

- raw tracker observations separately from calibrated, filtered and kinematic layers;
- explicit tracked, low-confidence and lost states;
- decoder presentation timestamps as the derivative time base;
- plate-diameter calibration and the manual seed used to define it;
- source SHA-256 and video/display geometry;
- effective tracker, filter and kinematics implementations/versions/parameters;
- for imported observations, the external tracker configuration and exact prediction-file SHA-256;
- deterministic FFmpeg frame-source provenance.

No wall-clock runtime or generated run identifier enters canonical analysis JSON. Repeated
execution over the same input and effective configuration is tested for byte-identical output.

`provenance.pipeline.git_commit` is compile-time provenance. CI sets `OPENBAR_GIT_COMMIT` to
`github.sha`, the same revision that the default `actions/checkout` step tests (the synthetic merge
revision for pull requests and the pushed revision for push runs). Uploaded M0 evidence therefore
identifies the code tree CI actually compiled without making the runtime environment alter otherwise
identical output. Local builds omit the optional commit unless `OPENBAR_GIT_COMMIT` is set when they
are compiled.

Calibration quality starts as `unassessed` because the CLI cannot infer camera geometry quality
from the pixels without evidence. That warning remains visible rather than being silently promoted
to a stronger claim.

## Output and diagnostics

Canonical JSON is written only to `--output`. Human diagnostics go to stderr. Existing output is
not overwritten unless `--force` is given.

`--diagnostics` accepts:

- `quiet`: no success/warning summary;
- `normal`: status and tracked/low-confidence/lost counts;
- `verbose`: normal output plus effective implementation identities and source hash.

A warning status does not change valid measurement data. It is emitted when recording support is warning/unknown, or when the run contains
low-confidence/lost samples, decoder diagnostics, or calibration-quality warnings. Normal diagnostics include the recording-support status and stable reason codes.

## Exit-code policy

The typed policy below applies to the #12 integration commands `analyze`, `benchmark`, and
`render`. The pre-existing `tracker-run`, `tracker-experiment`, and `filter-experiment`
validation harnesses retain their legacy generic `error:` + exit-code-2 behavior; reclassifying
their internal error surfaces is separate work and is not required to make the canonical analysis
pipeline reliable.

| Code | Category | Examples |
| ---: | --- | --- |
| 1 | `internal` | pipeline constructed an invalid canonical aggregate |
| 2 | `invalid-input` | unknown flag, inconsistent config, invalid filter/kinematics parameters |
| 3 | `media` | missing/unreadable video, FFmpeg failure, timestamp/frame mismatch |
| 4 | `unsupported` | unsupported rotation/sample aspect ratio or fixture geometry mismatch |
| 5 | `seed-calibration` | malformed/out-of-range seed, invalid plate calibration |
| 6 | `tracking` | selected tracker cannot execute the input |
| 7 | `output` | serialization/write/overwrite failure |
| 8 | `benchmark` | benchmark suite/data mismatch |

Invalid benchmark command-line syntax remains `invalid-input`; benchmark dataset/config failures
use `benchmark`.

## Validation

The public synthetic fixture is exercised end to end in Rust tests and CI. The internal-tracker
integration test runs the same analysis twice and compares bytes, round-trips the output through
the canonical reader, verifies the no-overwrite default, renders an SVG with a verified source
frame, and proves rendering leaves canonical analysis bytes unchanged. The external-observation
integration path likewise analyzes the committed `synthetic-perfect.prediction-v1.json` twice and
requires byte-identical canonical output; invalid imported media identity is also proven to leave
no analysis output. Focused importer tests cover wrong video hash, unmatched/non-monotonic
timestamps, duplicate decoded-frame bindings, missing seed sample, non-finite coordinates,
out-of-range confidence, invalid lost samples, unknown schema versions, invalid tracker identifiers,
and lossless preservation of large unsigned provenance integers. A second render fixture
preserves explicit lost/low-confidence state while filtered and kinematic layers are absent. CI
additionally validates generated/committed JSON against the versioned schemas.

FFmpeg remains an external runtime prerequisite under ADR-0006; no Cargo dependency is added.
