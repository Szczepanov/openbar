# M0 fixture dataset and provenance

Issue: #3

## Purpose

M0 needs repeatable video fixtures without turning the repository into an accidental
distribution channel for private or unlicensed media. Fixture metadata is therefore a
first-class validation artifact and is independent from the video bytes themselves.

The primary M0 validation domain is clean/snatch side-view footage. Back squats are a
lower-difficulty control condition. Oblique/45-degree, front and rear footage should be
retained as boundary or out-of-distribution material rather than treated as equivalent
measurement geometry.

## Canonical schema

The versioned JSON Schema is:

`validation/schema/fixture-manifest-v1.schema.json`

A manifest has:

- a top-level `schema_version`;
- stable fixture IDs;
- exercise and intended validation purpose;
- video timing/resolution metadata;
- camera view, approximate yaw/pitch/distance where known, and camera movement;
- load and known plate diameter;
- lighting, visibility, occlusion, motion blur and challenge tags;
- source/provenance and redistribution status;
- optional repository media path and SHA-256 identity.

The schema deliberately separates nominal/measured video metadata from later timestamp
observations. Timestamp-authoritative tracking/annotation data belongs to later M0
contracts rather than this fixture catalogue.

## Camera-view semantics

Use the following values consistently:

- `side` — true or near-side view intended for the M0 supported envelope;
- `oblique_45` — materially diagonal/approximately 45-degree footage;
- `front`;
- `rear`;
- `unknown`.

`approx_yaw_deg` should be recorded when it can be estimated without pretending to have
more precision than the source supports. The acceptable yaw envelope is a validation
result, not a schema assumption.

During M0, front/rear/oblique footage is normally classified as `boundary` or `ood`.
It must not be used to imply valid sagittal horizontal displacement unless later evidence
supports that geometry.

## Public vs private fixtures

### Public / redistribution-safe

Committed public fixture metadata lives in:

`validation/fixtures/public/manifest.json`

Media files are globally ignored by default. A media file should be force-added only when
all of the following are true:

1. redistribution is affirmatively permitted;
2. the manifest records `source.redistribution_status = "allowed"`;
3. rights/licence evidence is recorded;
4. the source and rights holder are identified where applicable;
5. adding the asset is consistent with OpenBar licensing and clean-room rules.

Accessibility on the internet is not redistribution permission.

### Private / local research

Private footage, including the project owner's personal lifting videos unless explicitly
cleared for redistribution, belongs under `validation/private/` and must remain
untracked.

Private manifests use the same schema. They should set redistribution status to
`private_only`, `prohibited`, or `unknown` as appropriate. Keeping the metadata
contract identical lets private footage participate in local experiments without creating
a second validation model.

## Adding a fixture

1. Assign a stable lowercase fixture ID.
2. Record metadata before running tracker experiments.
3. Classify the camera view and purpose.
4. Record the known plate diameter in metres.
5. Record provenance and redistribution status.
6. If media is public, document redistribution evidence before force-adding the binary.
7. If media is private, keep it under `validation/private/`.
8. Add challenge tags for conditions expected to matter in analysis.

Do not rename fixture IDs merely to make reports prettier. IDs become durable benchmark
keys once annotations/results refer to them.

## Example metadata

See:

`validation/examples/fixture-manifest.example.json`

The examples are metadata-only synthetic records. They intentionally include side-view
clean/snatch/control cases and an oblique boundary case; they do not grant or imply rights
to any external media.

## Manual target seed linkage

Manual target selections are intentionally separate artifacts rather than fields inside the
fixture catalogue. A standalone seed document may reference the stable fixture ID through
`fixture_id`:

- `validation/schema/manual-target-seed-v1.schema.json`;
- `validation/examples/manual-target-seed.example.json`.

This keeps fixture provenance/conditions stable while #5 benchmark cases can resolve the
same manual seed for multiple tracker implementations. See
[`MANUAL_TARGET_SEED.md`](MANUAL_TARGET_SEED.md) for coordinate, timestamp, geometry, and
validation semantics.

## Schema evolution

- Compatible additions may extend the v1 schema without changing existing field meaning.
- Breaking changes require a new schema version/file.
- Existing benchmark artifacts should continue to identify the schema version they were
  produced from.
- Avoid embedding tracker/filter-specific outputs in the fixture manifest; those belong in
  their own benchmark/analysis contracts.

## Explicitly out of scope

This foundation does not select a tracker, define annotation coordinates, implement plate
detection, or decide the final supported camera-angle envelope.
