# M0 tracker benchmark harness

Issue #5 establishes the common tracker benchmark contract used before selecting a production
tracker. The Rust core owns metric semantics; the headless CLI resolves versioned validation
artifacts and produces both machine-readable and human-readable results.

## Files and contracts

- `validation/schema/benchmark-suite-v1.schema.json` — suite/case references and evaluation policy.
- `validation/schema/tracker-prediction-v1.schema.json` — tracker output interchange format.
- `validation/schema/benchmark-result-v1.schema.json` — result artifact contract.
- `crates/openbar-core/src/benchmark.rs` — deterministic alignment and metric definitions.
- `validation/benchmarks/synthetic-tracker-smoke.benchmark-v1.json` — CI regression suite.

A benchmark case resolves:

1. a fixture from the fixture manifest;
2. timestamp-authoritative annotations;
3. a manual target seed when the tracker run requires one;
4. a named implementation, version, and configuration from the prediction artifact;
5. an inclusive selected time range;
6. timestamp matching tolerance and minimum accepted tracker confidence;
7. benchmark/pipeline provenance.

Paths inside a benchmark suite are resolved relative to the suite file. This lets local/private
suites use the same contract without committing private media.

## Coordinate and time semantics

Benchmark coordinates are display-oriented decoded pixels with origin at the top-left, +X right,
and +Y down, matching the annotation contract. Fixture dimensions and source identity are checked
before evaluation.

Timestamps, not nominal frame indices or nominal FPS, are authoritative. Annotation and prediction
streams must each be strictly increasing. The evaluator performs a monotonic, one-to-one nearest
timestamp match within the configured tolerance. A prediction can therefore match at most one
ground-truth sample. If two candidates are equally close, the earlier candidate wins because the
search is stable and ordered.

The benchmark does not interpolate coordinates to create a match.

## Which annotations are comparable

Within the selected range:

- `annotation_state=labelled` with a centre is comparable;
- visible and partially occluded labelled samples are both eligible;
- low/medium/high quality labelled samples remain eligible unless quality is explicitly
  `unusable`;
- `unlabelable`, `not_annotated`, and `unusable` samples are excluded from the metric
  denominator.

This means a deliberately unannotated or genuinely unlabelable frame cannot reward or punish a
tracker. Conversely, a tracker failure on a valid labelled frame is counted as loss.

## Tracking availability and loss

For every comparable annotation, exactly one of these outcomes is recorded:

- **tracked** — a timestamp-matched prediction is declared tracked and its confidence is at least
  the case threshold;
- **tracker-declared loss** — the matched prediction explicitly reports `lost`;
- **low confidence** — the prediction has coordinates but confidence is below the accepted threshold;
- **timestamp unmatched** — no prediction falls within the timestamp tolerance.

The last three outcomes are unavailable tracking samples.

Let:

- `N` = number of comparable annotations;
- `T` = number of usable tracked matches;
- `L = N - T`.

Then:

```text
tracking availability = T / N
lost-frame percentage = 100 * L / N
```

When `N = 0`, both values are `null`; zero would incorrectly imply measured failure or success.

Loss reasons are also reported separately as tracker-declared, low-confidence, and unmatched
counts. A lost/low-confidence/unmatched sample never receives an invented coordinate and never
contributes a zero coordinate error.

### Maximum consecutive loss

Loss spans are evaluated on the timestamp-ordered comparable annotation sequence.

- frame/sample length = number of consecutive unavailable comparable samples;
- duration starts at the first unavailable annotation timestamp;
- if tracking recovers, duration ends at the first subsequent available annotation timestamp;
- if loss continues through the selected sequence end, duration ends at the final comparable
  annotation timestamp.

This definition uses observed timestamps and therefore remains valid for irregular/VFR material.
A trailing single-sample loss has duration zero but still has a loss length of one sample.

## Coordinate error

Coordinate error exists only for usable tracked matches.

For tracked sample `i`:

```text
dx_i = predicted_x_i - truth_x_i
dy_i = predicted_y_i - truth_y_i
e_i  = sqrt(dx_i^2 + dy_i^2)
```

For `T > 0`:

```text
plate-centre MAE  = sum(e_i) / T
plate-centre RMSE = sqrt(sum(e_i^2) / T)
X MAE             = sum(abs(dx_i)) / T
X RMSE            = sqrt(sum(dx_i^2) / T)
Y MAE             = sum(abs(dy_i)) / T
Y RMSE            = sqrt(sum(dy_i^2) / T)
X bias            = sum(dx_i) / T
Y bias            = sum(dy_i) / T
```

Issue #14 also requires the shape of the error distribution rather than averages alone. Each case
therefore records radial-error p50, p90, p95 and maximum. Percentiles use the deterministic
nearest-rank definition over sorted tracked-sample radial errors: rank = ceil(p * T), one-indexed.
Lost/low-confidence/unmatched samples are represented by availability/loss fields and are never
inserted into the coordinate distribution as zero error.

When `T = 0`, coordinate and distribution metrics are `null`. Tracking loss is carried by
availability/loss metrics instead of fabricated coordinates.

## Aggregation

Results retain every case separately and additionally aggregate by implementation/configuration:

- overall;
- exercise;
- camera view;
- lighting;
- plate visibility;
- occlusion;
- motion blur;
- every fixture challenge tag.

Coordinate MAE/bias values are weighted by tracked sample count. Aggregate RMSE values are
reconstructed from per-case mean squared error weighted by tracked sample count. Availability/loss
uses summed sample counts. Maximum loss metrics take the maximum observed case value.

Exact pooled percentiles cannot be reconstructed from per-case percentile summaries, so p50/p90/p95
are intentionally `null` on aggregate rows. Per-case distributions remain authoritative evidence;
aggregate radial maximum is exact and is retained.

Different implementation configurations are separate groups even if they share a name/version.

## Runtime and determinism

Accuracy/loss metrics are deterministic for identical inputs.

Tracker runtime is intentionally separate. A tracker experiment may attach measured
`processing_wall_s` to its prediction artifact. The result then reports selected media duration
and media-seconds processed per wall-second. Wall-clock runtime is environment-sensitive and is
not part of deterministic metric equality or accuracy regression gates.

The result also records CLI version, OS, architecture, pipeline version, and git commit when
provided or available from CI.

## Empty and unsupported cases

- No comparable annotations: coordinate and rate metrics are `null`, and the result carries a
  warning.
- Comparable annotations but no usable tracking: coordinate metrics are `null`; loss is 100%;
  the result carries a warning.
- Invalid source identity, dimensions, manual seed, timestamp ordering, confidence, coordinates,
  or selected range fail the run explicitly rather than being coerced.
- Prediction coordinates outside the fixture frame are rejected.
- Lost predictions must not carry centre/confidence fields.

## Synthetic regression case

The committed synthetic suite contains two named implementations over the same annotation set:

- `synthetic-perfect@1` — exact coordinates, 100% availability, zero MAE/RMSE;
- `synthetic-offset-loss@1` — five 3/4-pixel offset samples (5 px radial error), one declared
  loss, one low-confidence sample, and one timestamp-unmatched sample.

The second case has 10 comparable annotations, 7 usable tracked samples, 70% availability,
30% lost frames, MAE `25/7` px, RMSE `sqrt(125/7)` px, X bias `9/7` px, and Y bias
`20/7` px. These values are asserted in Rust tests.

The synthetic runtime values exist only to exercise serialization; they are not performance
evidence.

## CLI

Run the committed regression suite:

```bash
cargo run -p openbar-cli -- benchmark \
  --suite validation/benchmarks/synthetic-tracker-smoke.benchmark-v1.json \
  --output target/benchmark-smoke.json
```

The concise report is written to stderr. The versioned JSON artifact is written to
`--output`; without `--output`, JSON is written to stdout.

## Future metric extensions

Filter and kinematic validation should extend the result model without changing tracker error
semantics. Expected later additions include calibrated position/ROM error, mean/peak velocity
error, peak attenuation, filter edge behaviour, and latency. Raw tracker availability/error
metrics should remain independently reproducible when those layers are added.
