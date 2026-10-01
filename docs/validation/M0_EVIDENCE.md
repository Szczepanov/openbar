# M0 evidence package

Issue #14 is the evidence-generation stage of M0. The prerequisite implementation work through
#13 and #40 is now present on `main`: decoded video can reach both tracker baselines, canonical
analysis can run end to end, filtering and kinematic semantics are versioned, and diagnostic
rendering consumes canonical analysis without recomputing measurements.

That means the remaining question is no longer whether the pipeline can execute. It is whether the
available evidence is strong enough to decide the provisional M0 gates.

## Current evidence boundary

The public repository currently contains one annotated video fixture,
`synthetic-clean-side-12`. It is a deterministic synthetic development fixture intended to exercise
contracts, decode, tracking, failure representation and annotation workflow. Its manifest explicitly
states that it is not a substitute for tracker-accuracy validation on real lifting footage.

The tracker and filter work reached the same conclusion independently:

- #7 carries both tracker baselines forward until annotated real footage exists;
- #10 defers a production filter winner until real decoded/reference evidence exists;
- #11 validates kinematic definitions and analytic behaviour but still requires matched real/reference
  measurements before its provisional error gates can be claimed.

Accordingly, the M0 evidence pipeline must not convert successful synthetic smoke runs into a claim
that the real-world accuracy gates passed.

## Reproducible public preflight

CI produces a deterministic evidence preflight after the existing M0 smoke pipeline:

1. decode the committed synthetic video and run both tracker baselines;
2. benchmark those **actual decoded tracker predictions** against the committed annotations;
3. execute canonical `analyze` twice with identical effective configuration and require byte-identical
   JSON;
4. collect the tracker/filter experiment artifacts without reimplementing their measurement logic;
5. generate `m0-evidence-v1` JSON plus a human-readable Markdown report;
6. validate the JSON against `validation/schema/m0-evidence-v1.schema.json`;
7. upload the evidence alongside the existing M0 smoke artifacts.

The decoded-tracker suite is
`validation/benchmarks/synthetic-decoded-trackers.benchmark-v1.json`. It is intentionally separate
from `synthetic-tracker-smoke.benchmark-v1.json`: the latter tests benchmark semantics with authored
prediction documents, while the decoded suite evaluates predictions actually emitted by the two
trackers after FFmpeg decoding.

The evidence command is:

```bash
python validation/tools/m0_evidence.py \
  --manifest validation/fixtures/public/manifest.json \
  --annotation validation/fixtures/public/annotations/synthetic-clean-side-12.annotation-v1.json \
  --decoded-tracker-benchmark target/tracker-decoded-benchmark.json \
  --tracker-experiment target/tracker-experiment.json \
  --filter-experiment target/filter-experiment.json \
  --analysis target/analyze-smoke.json \
  --analysis-repeat target/analyze-smoke-repeat.json \
  --output-json target/m0-evidence.json \
  --output-markdown target/m0-evidence.md
```

`m0_evidence.py` is reporting-only. It hashes and inventories authoritative artifacts and never
recomputes tracker, calibration, filtering or kinematic measurements in Python.

## Gate policy

Every provisional M0 gate is always present in the generated report with one of the statuses required
by #14: `PASS`, `FAIL`, `NOT_MEASURABLE_YET`, or `REVISED`.

For the current public corpus the expected interpretation is:

| Gate | Current public-evidence status | Reason |
| --- | --- | --- |
| Plate-centre tracking MAE < 3 px | `NOT_MEASURABLE_YET` | no annotated non-synthetic held-out validation fixture |
| Tracking availability > 99% | `NOT_MEASURABLE_YET` | same evidence gap; synthetic decoded results remain diagnostics |
| ROM MAE < 0.01 m | `NOT_MEASURABLE_YET` | no matched non-synthetic calibrated position/ROM reference |
| Mean velocity MAE < 0.05 m/s | `NOT_MEASURABLE_YET` | no matched semantically equivalent velocity reference |
| Peak velocity MAE < 0.10 m/s | `NOT_MEASURABLE_YET` | no matched signed-axis peak-velocity reference |
| Repeat-analysis determinism = 100% | measured by the preflight | two independent canonical outputs are compared byte-for-byte |
| Offline processing faster than video duration | `NOT_MEASURABLE_YET` | CI runtime is not the documented M0 reference-hardware benchmark |

A synthetic tracker can therefore be numerically excellent in the preflight and still leave the
real-world tracking gate undecided. This is intentional.

## What still blocks #14

The preflight makes the missing evidence explicit rather than silently treating it as success. To
complete #14, the project still needs at minimum:

- a sufficiently representative annotated non-synthetic validation corpus, separate from tuning as
  far as M0 scale allows;
- a frozen tracker/filter candidate configuration evaluated on that corpus;
- matched calibrated position/ROM references and semantically equivalent velocity references where
  the corresponding gates are to be decided;
- runtime measurement on documented reference hardware;
- condition-level breakdowns and representative failure review using #13 diagnostics.

Only after that evidence exists should #15 turn measured behaviour into a supported recording
envelope, and #16 make the explicit M0 go/conditional-go/no-go decision.
