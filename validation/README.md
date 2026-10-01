# Validation assets

This directory holds the machine-readable inputs used to validate OpenBar M0.

## Layout

- `schema/fixture-manifest-v1.schema.json` — canonical M0 fixture metadata schema.
- `fixtures/public/manifest.json` — committed manifest for redistribution-safe fixtures.
- `examples/fixture-manifest.example.json` — metadata-only examples covering primary and boundary conditions.
- `private/` — local-only research material; ignored by Git.

The detailed policy and workflow live in
[`docs/validation/FIXTURE_DATASET.md`](../docs/validation/FIXTURE_DATASET.md).

Do not add public media merely because it is technically accessible. Every committed media
fixture must have affirmative redistribution rights documented in its manifest metadata.
