# M0 independent physical/reference kinematic study

Issue: #58

## Purpose

The `kinematic-reference` command turns synchronized independent physical/reference observations
into reproducible evidence for the three M0 kinematic gates:

| Gate | Preserved provisional target |
| --- | ---: |
| Range-of-motion MAE | < 0.01 m |
| Mean signed-axis velocity MAE | < 0.05 m/s |
| Peak signed-axis velocity MAE | < 0.10 m/s |

This is validation tooling, not validation evidence by itself. The repository does **not** currently
contain a qualifying independent physical/reference dataset, so #58 remains incomplete until such a
study is captured/imported and its result/report are reviewed.

GitHub currently marks #58 closed; that issue state must not be interpreted as physical-validation
completion. The 2026-10-05 bar-path experiment confirms that no independent reference measurements
were supplied for its development runs. Tooling delivery and a reviewed physical study remain
separate milestones; the kinematic gates are not moved to PASS by software completion.

Synthetic analytic fixtures remain useful for implementation semantics and regression testing. They
must not be labelled as independent physical evidence or used to move these gates to PASS.

## Command

```bash
cargo run --locked -p openbar-cli -- kinematic-reference \
  --study validation/private/reference-study/study.json \
  --output target/kinematic-reference-result.json \
  --report target/KINEMATIC_REFERENCE_REPORT.md

python validation/tools/schema_check.py \
  --schema validation/schema/kinematic-reference-result-v1.schema.json \
  target/kinematic-reference-result.json
```

Private source measurements, videos and canonical analyses stay under `validation/private/` and are
not committed. A shareable study may commit only material for which redistribution rights are
explicitly documented.

## Study contract

The input is `kinematic-reference-study-v1`. It deliberately accepts only
`evidence_class = independent_physical_reference`.

A study records:

- reference source type and system;
- measurement protocol;
- synchronization method;
- coordinate alignment into OpenBar's reference-centre-relative metric axis;
- rights/access statement;
- the axis under test;
- exact interval semantics;
- canonical mean and peak definitions;
- exact-timestamp alignment policy;
- plate-diameter calibration method/version;
- `backward-difference@1` parameters;
- expected tracker and, when applicable, filter provenance for every supported case;
- reference position/velocity uncertainty and how it was obtained;
- condition labels and unsupported cases.

The v1 evaluator accepts independent reference families such as a controlled geometric rig,
encoder/linear position transducer, high-frame-rate reference measurement, or a validated VBT
device. A VBT/device result is only comparable when its reported construct can be mapped to the
frozen OpenBar construct without changing the definition.

## Coordinate and synchronization requirements

Reference samples are one-dimensional physical positions for the declared axis. Before they enter
the study JSON they must be expressed in the same sign/origin convention as OpenBar:

```text
reference_centre_x_right_y_up
```

For the common vertical study, +Y is upward and positions are metres relative to the declared
reference centre. Any transform from the physical system's native coordinate frame must be described
in `reference.coordinate_alignment`.

v1 requires each reference timestamp to match an authoritative OpenBar sample timestamp exactly.
The evaluator does not interpolate either series and does not substitute nominal FPS. If a source
requires synchronization, resampling or timestamp mapping before comparison, the protocol must
document that preprocessing and its uncertainty.

## OpenBar provenance checks

Every supported case points to a canonical `analysis-v1` JSON. Before computing metrics the
evaluator fails the case unless the analysis matches the frozen study contract:

- tracker implementation/version/parameters equal `expected_tracker`;
- calibration is the declared plate-diameter method/version and coordinate convention;
- the chosen velocity layer is the canonical kinematics input;
- a filtered velocity layer exactly matches `expected_filter`;
- persisted kinematics are `backward-difference@1` with the frozen gap/confidence parameters.

The canonical analysis parser already validates raw/calibrated/filtered/kinematic alignment and
re-derives canonical velocity when the method claims `backward-difference@1`.

## Metric semantics

For each evaluated case, calibrated OpenBar positions are exact-timestamp matched to the independent
reference positions.

The report records the signed position-error distribution:

```text
error = OpenBar calibrated position - independent reference position
```

and reports count, MAE, RMSE, signed bias, p50/p90/p95 absolute error and maximum absolute error.

ROM is computed by the authoritative core `range_of_motion` implementation from the calibrated
OpenBar layer and the matched reference series.

Mean signed-axis velocity and peak signed-axis velocity are computed by the authoritative core
`mean_axis_velocity` and `peak_axis_velocity` functions over the same explicit interval and using
the frozen canonical kinematics configuration. The selected OpenBar velocity layer may be calibrated
or filtered, but its provenance is pinned per case and persisted in the result.

## Failures and unsupported conditions

Cases have one of two declared input states:

- `supported`: this recording/reference pair is expected to satisfy the frozen study envelope;
- `unsupported`: the reason is recorded and the case is never included in gate averages.

A supported case that cannot be compared exactly, has mismatched provenance, or yields unavailable
ROM/mean/peak metrics becomes an output `failed` case. **Any failed supported case forces all three
kinematic gates to FAIL.** The tool never drops that case and recomputes a cleaner average.

Unsupported cases remain in the JSON and Markdown report with their reason and sample count. They
do not contribute to MAE because the study explicitly says the construct is unsupported there.

## Gate evaluation

For each gate, evaluated case-level signed errors are summarized as an error distribution. MAE is
the mean of the absolute case errors; bias is the mean signed case error.

Without a revision:

- `PASS` means observed MAE is strictly below the preserved provisional target and there are no
  failed supported cases;
- `FAIL` means the MAE misses the target, there is no evaluated evidence, or at least one
  supported case failed.

A study may record a target revision only through `target_revisions`. A revision must include a
different positive threshold, rationale, and evidence reference. The result then reports:

- status `REVISED`;
- the original threshold unchanged;
- the effective revised threshold;
- whether the observed result meets the effective threshold.

`REVISED` is not a synonym for PASS.

## Output and reproducibility

The command writes:

1. `kinematic-reference-result-v1` JSON containing case metrics, gate distributions/status,
   uncertainty, failures/unsupported conditions and complete OpenBar provenance snapshots;
2. a human-readable Markdown report generated from the same result object.

No wall-clock timestamp or random run identifier is added, so identical study inputs and identical
canonical analysis files produce byte-stable measurement/report content.

The result is deliberately scope-bounded. A PASS applies only to the recorded source system,
conditions, axis, intervals, synchronization, calibration, tracker/filter configuration and
kinematic method. It must not be generalized to unsupported recording conditions, automatic
detection, or a broader product accuracy claim.

## Recommended physical study

A strong first study would use a controlled vertical motion or barbell trial with a synchronized
encoder/LPT (or another independently calibrated position reference):

1. keep the camera fixed in the current side-view recording envelope;
2. record the reference position signal and phone video from a common or measurable time base;
3. quantify reference distance and timing uncertainty;
4. transform reference samples into OpenBar's +Y-up, reference-centred metres;
5. map reference observations to exact authoritative video timestamps with the mapping method
   documented;
6. run the frozen OpenBar pipeline and preserve its canonical analysis JSON;
7. repeat across enough trials/conditions to expose error distribution and representative failures;
8. run `kinematic-reference`, validate the result schema, and review the generated report before
   changing any M0 gate status.

High-frame-rate reference video can be used when its own calibration, digitisation repeatability and
timing uncertainty are documented. A commercial VBT source should be used only when the mean/peak
construct, phase boundaries, smoothing/filtering and timestamp semantics are definition-matched.


## Agreement-study design requirements

Commercial VBT applications can be useful secondary comparators, but agreement with another app is
not automatically ground truth. A commercial-device/app comparison is acceptable only when the
metric construct, phase boundaries, filtering/smoothing, axis/sign convention and synchronization
can be mapped to the frozen OpenBar definition. Independent physical/reference evidence remains the
preferred basis for M0 gate claims.

### Explicit observation pairing

Never pair repetitions merely by row position or by truncating two result arrays to the shorter
length. A missed or extra repetition can otherwise shift every subsequent comparison.

A validation import should identify comparable observations by an explicit key or exact interval,
for example:

```text
participant / session / set / repetition
+ measurement interval
+ construct definition
```

or by the exact timestamp-alignment rules already required by this study.

Missed detections, extra detections and unmatchable intervals are outcomes to report, not rows to
drop silently.

### Repeated observations

Multiple repetitions from one athlete/session are clustered observations. Early engineering studies
may still report descriptive per-rep errors, but formal inferential uncertainty must not pretend all
repetitions are independent.

As study size grows, use an analysis appropriate to the hierarchy, such as cluster/bootstrap
confidence intervals, mixed-effects modelling, or another pre-specified repeated-measures method.
Record the participant/session/exercise/load grouping needed to reproduce that analysis.

### Agreement reporting

For sufficiently sized studies, report more than correlation:

- signed bias;
- 95% limits of agreement;
- confidence intervals for bias/limits where justified by the design;
- MAE/RMSE and absolute-error percentiles;
- misses/extra detections separately from matched-value error;
- condition-stratified results;
- participant/session/exercise/load counts;
- reference-system uncertainty.

Correlation/regression can remain descriptive but must not replace agreement/error analysis.

### Scope of conclusions

A study based on one athlete, one exercise or one camera geometry may be useful development evidence
but cannot establish general product validity. The report must state the population, exercises,
loads, devices, frame rates and recording conditions actually represented and avoid extrapolating
outside them.
