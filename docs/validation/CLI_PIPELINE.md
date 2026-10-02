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

There is deliberately **no default tracker or production filter**. The caller must choose both.
Filtering evidence from #10 explicitly deferred a production winner; the CLI therefore cannot
silently promote a candidate. Filter-specific parameters are required where applicable and
irrelevant parameters are rejected. Tracker family defaults may be used after the family itself is
selected, and the complete effective tracker configuration is persisted in analysis provenance.

The kinematics continuity threshold and minimum-confidence threshold are also required explicitly.

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

The public synthetic fixture is exercised end to end in Rust tests and CI. The integration test
runs the same analysis twice and compares bytes, round-trips the output through the canonical
reader, verifies the no-overwrite default, renders an SVG with a verified source frame, and proves
rendering leaves canonical analysis bytes unchanged. A second render fixture preserves explicit
lost/low-confidence state while filtered and kinematic layers are absent. CI additionally validates
the generated analysis artifact against `validation/schema/analysis-v1.schema.json` and uploads
both success and failure SVG diagnostics.

FFmpeg remains an external runtime prerequisite under ADR-0006; no Cargo dependency is added.
