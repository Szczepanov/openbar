# Validation assets

This directory holds the machine-readable inputs and tooling used to validate OpenBar M0.

## Layout

- `schema/fixture-manifest-v1.schema.json` — canonical M0 fixture metadata schema.
- `schema/manual-target-seed-v1.schema.json` — standalone serialization contract for the Rust manual seed type.
- `schema/annotation-v1.schema.json` — timestamp-authoritative plate-centre annotation schema.
- `schema/benchmark-suite-v1.schema.json` — benchmark suite/case contract.
- `schema/tracker-prediction-v1.schema.json` — tracker prediction interchange contract.
- `schema/benchmark-result-v1.schema.json` — versioned benchmark result artifact.
- `schema/m0-evidence-v1.schema.json` — issue #14 aggregate public evidence/gate-status contract.
- `schema/kinematic-reference-study-v1.schema.json` — independent physical/reference study input contract for #58.
- `schema/kinematic-reference-result-v1.schema.json` — machine-readable ROM/velocity reference-study result contract.
- `schema/analysis-v1.schema.json` — structural wire contract for the canonical Rust `Analysis` (see `docs/data/ANALYSIS_SCHEMA.md`).
- `fixtures/public/manifest.json` — committed manifest for redistribution-safe fixtures.
- `fixtures/public/annotations/` — canonical annotations/repeatability artifacts for public fixtures.
- `fixtures/public/seeds/` — manual target seeds attached to public benchmark fixtures.
- `fixtures/public/predictions/` — synthetic/golden prediction streams; real experiment outputs may remain local.
- `benchmarks/` — reusable benchmark suite definitions.
- `examples/fixture-manifest.example.json` — metadata-only fixture examples.
- `examples/manual-target-seed.example.json` — a seed linked to the synthetic example fixture ID.
- `examples/annotation-import-metadata.example.json` + `annotation.example.csv` — minimal annotation import example.
- `tools/annotations.py` — deterministic stdlib-only importer, validator, and repeatability metric tool.
- `tools/label_package.py` + `tools/label_page.html` — optional stdlib-only helper that extracts frames on a
  uniform time grid and builds a local click-to-label page exporting the `import-csv` format.
- `tools/schema_check.py` — stdlib-only JSON Schema checker; validates every committed fixture against its schema and fails on unmapped JSON files.
- `tests/` — annotation and schema-check contract/tooling tests.
- `private/` — local-only research material; ignored by Git.

The first tracker comparison is generated headlessly with:

```bash
cargo run -p openbar-cli -- tracker-experiment --output target/tracker-experiment.json
```

The filter comparison is generated with:

```bash
cargo run -p openbar-cli -- filter-experiment --output target/filter-experiment.json
```

Independent physical/reference kinematic evidence is evaluated with:

```bash
cargo run --locked -p openbar-cli -- kinematic-reference \\
  --study validation/private/reference-study/study.json \\
  --output target/kinematic-reference-result.json \\
  --report target/KINEMATIC_REFERENCE_REPORT.md
```

This command is intentionally not part of the synthetic CI smoke evidence: #58 requires genuinely
independent physical/reference observations. See
[`M0_REFERENCE_STUDY.md`](../docs/validation/M0_REFERENCE_STUDY.md).

The complete reproducible public M0 evidence package is generated with:

```bash
python3 validation/tools/m0_evidence.py --output-dir target/m0-evidence
python3 validation/tools/schema_check.py \
  --schema validation/schema/m0-evidence-v1.schema.json \
  target/m0-evidence/m0-evidence-v1.json
```

The evidence runner repeats tracker and canonical analysis execution, benchmarks both retained M0
tracker baselines against the same public annotations, records environment/runtime provenance,
generates diagnostic renders, and assigns an explicit status to every provisional M0 gate. It does
not reinterpret unavailable real-world evidence as a PASS.

Add `--private-manifest validation/private/manifest.json` to also evaluate local real clips with a
release build. Private results are aggregates only (`M0_PRIVATE_EVIDENCE_REPORT.md`).

CI retains the filter JSON inside the hardened `m0-smoke-<head-sha>` workflow artifact alongside
tracker and benchmark-smoke evidence. The retained filter JSON is deterministic measurement/contract
evidence; environment-sensitive runtime is printed to the console and intentionally excluded from
that JSON. Both experiment artifacts remain separate from real-video M0 validation results.

Detailed policies and workflows:

- [`docs/validation/FIXTURE_DATASET.md`](../docs/validation/FIXTURE_DATASET.md)
- [`docs/validation/MANUAL_TARGET_SEED.md`](../docs/validation/MANUAL_TARGET_SEED.md)
- [`docs/validation/ANNOTATION.md`](../docs/validation/ANNOTATION.md)
- [`docs/validation/BENCHMARK.md`](../docs/validation/BENCHMARK.md)
- [`docs/validation/TRACKER_EXPERIMENTS.md`](../docs/validation/TRACKER_EXPERIMENTS.md)
- [`docs/validation/FILTER_EXPERIMENTS.md`](../docs/validation/FILTER_EXPERIMENTS.md)
- [`docs/validation/M0_EVIDENCE.md`](../docs/validation/M0_EVIDENCE.md)
- [`docs/validation/M0_EVIDENCE_REPORT.md`](../docs/validation/M0_EVIDENCE_REPORT.md)
- [`docs/validation/M0_PRIVATE_EVIDENCE_REPORT.md`](../docs/validation/M0_PRIVATE_EVIDENCE_REPORT.md)

Do not add public media merely because it is technically accessible. Every committed media
fixture must have affirmative redistribution rights documented in its manifest metadata.
