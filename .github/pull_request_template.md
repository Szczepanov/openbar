## Summary

<!--
What changed and why? Link the issue with "Closes #NN" only when this PR fully resolves it.
Keep this focused on behaviour, contracts, validation evidence, or repository governance rather
than listing files.
-->

## Linked work and scope

<!--
Related issues / ADRs / validation docs:
Out of scope / follow-up:
-->

## Measurement and contract impact

<!-- Check the statements that are true and explain any non-trivial impact below. -->

- [ ] No change to measurement output, serialized contracts, schema/version constants, or benchmark semantics.
- [ ] Measurement behaviour changes and deterministic regression evidence is included.
- [ ] Serialized shape/semantics changes: the relevant version was bumped and schemas/docs/fixtures were updated.
- [ ] Public fixtures, annotations, seeds, predictions, or golden files change intentionally and provenance/redistribution rights are documented.
- [ ] A dependency changes: source, licence, purpose, distribution compatibility, and `deny.toml` impact were reviewed.

Details:

## Validation

<!--
Record what actually ran. Do not rely on "CI will check it" as the only evidence.
Use focused tests during development, then the applicable repository gates before handoff.
-->

- [ ] `cargo fmt --all -- --check`
- [ ] `cargo clippy --locked --workspace --all-targets --all-features -- -D warnings`
- [ ] `cargo build --locked --workspace --all-targets --all-features`
- [ ] `cargo test --locked --workspace --all-targets --all-features`
- [ ] `python -m unittest discover -v -s validation/tests -p 'test_*.py'`
- [ ] `python validation/tools/schema_check.py`
- [ ] Scope-specific tracker/filter/analyze/benchmark/render/evidence checks completed, or not applicable.

Results:

```text
command: pass/fail — what it covered
```

## Risk and reviewer guidance

<!--
Identify the highest-risk seam, plausible regression, compatibility concern, and rollback path.
For low-risk changes, say why they are low risk.
-->

## Architecture and M0 invariants

<!-- Mark relevant items and explain exceptions. -->

- [ ] Raw observations remain preserved; derived/filtered values do not overwrite them.
- [ ] Missing/lost/low-confidence tracking remains explicit; no fabricated coordinates or silent long-gap interpolation.
- [ ] Timestamps remain authoritative for time-dependent logic; units are explicit.
- [ ] Same input + pipeline version remains reproducible/deterministic.
- [ ] Authoritative domain logic stays in `openbar-core`; CLI/tracking/validation layers do not duplicate it.
- [ ] Durable architecture/behaviour changes are reflected in ADRs/docs.
- [ ] Change stays inside current M0 scope, or the scope decision is explicitly reopened and documented.

## Data, licensing, and clean-room review

- [ ] No credentials, secrets, private user data, or unapproved media/data/model artifacts are committed.
- [ ] Any external code/data/model/material referenced by this PR has source and licence recorded, with compatibility reviewed.
- [ ] Competitor behaviour may inform requirements only; no proprietary implementation, assets, text, models, datasets, or non-public details were copied.
- [ ] Contributor-rights requirements in `CONTRIBUTING.md` are satisfied before merge.

## Evidence / artifacts

<!--
Link benchmark outputs, diagnostic renders, M0 evidence artifacts, screenshots, recordings, or
"Not applicable — <reason>".
-->
