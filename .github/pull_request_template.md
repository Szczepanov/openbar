## Summary

<!-- What changes and why. Link the issue: "Closes #NN" or "Part of #NN". -->

## Measurement and contract impact

<!-- Delete the lines that don't apply and explain the ones that do. -->

- [ ] No change to measurement output, serialized shape or any `*_VERSION` constant.
- [ ] Changes serialized shape/semantics: version bumped, `validation/schema/*.schema.json`,
      `docs/data/` or `docs/validation/` and fixtures updated in this PR.
- [ ] Changes `analysis-v1.golden.json` or anything under `validation/fixtures/` (intentional; reason below).
- [ ] Adds a dependency: source, licence, purpose and distribution compatibility documented
      (ARCHITECTURE.md "Dependency policy"), and `deny.toml` passes.

## Checklist (from the M0 umbrella issue #2)

- [ ] Extends existing Rust types rather than adding a parallel model.
- [ ] Deterministic unit tests, including error paths and edge cases.
- [ ] Fixture/golden/integration tests where behaviour crosses modules.
- [ ] Raw inputs and provenance are preserved; derived values sit alongside raw data.
- [ ] Missing, lost and low-confidence data stay explicit (no fake coordinates, no silent gap filling).
- [ ] Timestamps, not frame index or nominal FPS, drive time-dependent logic.
- [ ] Docs/ADRs updated where durable behaviour or architecture changed.
- [ ] Domain logic lives in `openbar-core`; no out-of-scope M0 features (see AGENTS.md).

## Verification

<!-- Paste the commands you ran and their results. CI runs the full list from AGENTS.md. -->

```text
cargo fmt --all -- --check
cargo clippy --locked --workspace --all-targets --all-features -- -D warnings
cargo test --locked --workspace --all-targets --all-features
python -m unittest discover -v -s validation/tests -p 'test_*.py'
python validation/tools/schema_check.py
```
