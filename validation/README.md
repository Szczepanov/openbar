# Validation assets

This directory holds the machine-readable inputs and tooling used to validate OpenBar M0.

## Layout

- `schema/fixture-manifest-v1.schema.json` — canonical M0 fixture metadata schema.
- `schema/manual-target-seed-v1.schema.json` — standalone serialization contract for the Rust manual seed type.
- `schema/annotation-v1.schema.json` — timestamp-authoritative plate-centre annotation schema.
- `fixtures/public/manifest.json` — committed manifest for redistribution-safe fixtures.
- `fixtures/public/annotations/` — canonical annotations/repeatability artifacts for public fixtures.
- `examples/fixture-manifest.example.json` — metadata-only fixture examples.
- `examples/manual-target-seed.example.json` — a seed linked to the synthetic example fixture ID.
- `examples/annotation-import-metadata.example.json` + `annotation.example.csv` — minimal annotation import example.
- `tools/annotations.py` — deterministic stdlib-only importer, validator, and repeatability metric tool.
- `tests/` — annotation contract/tooling tests.
- `private/` — local-only research material; ignored by Git.

Detailed policies and workflows:

- [`docs/validation/FIXTURE_DATASET.md`](../docs/validation/FIXTURE_DATASET.md)
- [`docs/validation/MANUAL_TARGET_SEED.md`](../docs/validation/MANUAL_TARGET_SEED.md)
- [`docs/validation/ANNOTATION.md`](../docs/validation/ANNOTATION.md)

Do not add public media merely because it is technically accessible. Every committed media
fixture must have affirmative redistribution rights documented in its manifest metadata.
