# AGENTS.md

Guidance for AI coding agents working in this repository. Humans should start with
[README.md](README.md), [VISION.md](VISION.md) and [CONTRIBUTING.md](CONTRIBUTING.md); this
file condenses the rules that matter when changing code.

## Project in one paragraph

OpenBar is a local-first barbell video-analysis engine. Current phase: **M0 — Validated
Bar-Path Engine** (pre-alpha, headless only). Given side-view video, a manual plate seed, and a
known plate diameter, it tracks the plate, calibrates to metres, derives kinematics, and makes
uncertainty explicit. Measurement correctness and reproducibility outrank features.

## Layout

| Path | What lives there |
|------|------------------|
| `crates/openbar-core` | Authoritative deterministic domain logic: `analysis` (canonical versioned `Analysis` aggregate), `calibration`, `manual_seed`, `trajectory`, `filtering`, `kinematics`, `benchmark` (metric semantics), `math`. No UI/media/ML deps. |
| `crates/openbar-tracking` | Decoder-agnostic M0 tracker experiments (`template`, `contrast`) behind the `GrayscaleImage` / `FrameSample` boundary. Depends only on `openbar-core`. |
| `apps/openbar-cli` | Headless CLI + validation harness: `analyze` (canonical M0 pipeline), `benchmark`, deterministic diagnostic `render`, `tracker-experiment`, `filter-experiment`, and `tracker-run` (real video through ADR-0006 FFmpeg in `src/media/`). Hand-rolled arg parsing, no clap. |
| `validation/` | JSON schemas, public fixtures (manifest, annotations, seeds, predictions), benchmark suites, and the stdlib-only Python annotation tool + tests. `validation/private/` is git-ignored. |
| `docs/adr/` | Accepted architecture decisions. Read the relevant ADR before changing a boundary. |
| `docs/validation/` | Contracts for fixtures, seeds, annotations, calibration, benchmark, tracker experiments. |

Future crates (`openbar-inference`, `openbar-video`), the `ml/` Python workspace, and the
Flutter UI do **not** exist yet. Do not scaffold them unless asked.

## Commands

Toolchain is pinned by `rust-toolchain.toml` (Rust 1.98.1, clippy + rustfmt). CI runs exactly
these; run them before declaring work done:

```bash
cargo fmt --all -- --check
cargo clippy --locked --workspace --all-targets --all-features -- -D warnings
cargo test --locked --workspace --all-targets --all-features
cargo run --locked -p openbar-cli -- tracker-experiment --output target/tracker-experiment.json
cargo run --locked -p openbar-cli -- filter-experiment --output target/filter-experiment.json
cargo run --locked -p openbar-cli -- tracker-run --manifest validation/fixtures/public/manifest.json --fixture synthetic-clean-side-12 --seed validation/fixtures/public/seeds/synthetic-clean-side-12.manual-target-seed-v1.json --output-dir target/tracker-run-smoke
cargo run --locked -p openbar-cli -- analyze --manifest validation/fixtures/public/manifest.json --fixture synthetic-clean-side-12 --seed validation/fixtures/public/seeds/synthetic-clean-side-12.manual-target-seed-v1.json --plate-diameter-m 0.45 --tracker template --filter raw --kinematics-max-gap-s 0.2 --kinematics-min-confidence 0 --output target/analyze-smoke.json
cargo run --locked -p openbar-cli -- benchmark --suite validation/benchmarks/synthetic-tracker-smoke.benchmark-v1.json --output target/benchmark-smoke.json
cargo run --locked -p openbar-cli -- benchmark --suite validation/benchmarks/synthetic-decoded-trackers.benchmark-v1.json --output target/tracker-decoded-benchmark.json
python validation/tools/m0_evidence.py --manifest validation/fixtures/public/manifest.json --annotation validation/fixtures/public/annotations/synthetic-clean-side-12.annotation-v1.json --decoded-tracker-benchmark target/tracker-decoded-benchmark.json --tracker-experiment target/tracker-experiment.json --filter-experiment target/filter-experiment.json --analysis target/analyze-smoke.json --analysis-repeat target/analyze-smoke-repeat.json --output-json target/m0-evidence.json --output-markdown target/m0-evidence.md
```

`tracker-run` and the `media::` tests need `ffmpeg` and `ffprobe` on `PATH`. Decoding is an
external process (ADR-0006): do not add FFmpeg bindings or wrapper crates. Locally, missing
FFmpeg makes the decode tests print `SKIPPED`. CI sets `OPENBAR_REQUIRE_FFMPEG=1` so they fail
instead.

Scoped iteration: `cargo test -p openbar-core`, `cargo test -p openbar-tracking`,
`cargo test -p openbar-core analysis::` (filter by module path).

Python validation tooling (stdlib only, Python 3.14 in CI — no third-party packages):

```bash
python -m unittest discover -v -s validation/tests -p 'test_*.py'
python validation/tools/annotations.py validate --manifest validation/fixtures/public/manifest.json validation/fixtures/public/annotations/synthetic-clean-side-12.annotation-v1.json
python validation/tools/schema_check.py
```

`schema_check.py` validates every committed JSON fixture against its `validation/schema/` schema.
It fails if a JSON file under `validation/fixtures/public`, `validation/examples`,
`validation/benchmarks` or `crates/openbar-core/tests/fixtures` has no schema mapping, so a new
fixture type means adding a schema and a `CATALOGUE` entry, or an explicit `SCHEMALESS` reason.
It supports only the keyword subset the schemas already use and fails closed on any other keyword.

CI also runs the workspace tests and both smoke commands on Windows, and runs
`cargo deny --all-features --locked check` (config in `deny.toml`). A new dependency whose
licence isn't on the `deny.toml` allow-list fails CI. Extend the list only alongside the
dependency-policy justification.

`annotations.py` subcommands: `validate`, `import-csv`, `repeatability`.

## Non-negotiable measurement rules

These come from VISION.md and ADR-0003/0005. Violating them is a bug even if tests pass.

- **Raw is never replaced.** Filtered/derived values sit alongside raw observations, never
  overwrite them.
- **Lost means no coordinate.** A lost/untracked sample carries no fake position or bounds. Do
  not silently interpolate gaps.
- **Timestamps are authoritative**, not frame index or nominal FPS.
- **Determinism.** Same inputs + pipeline version ⇒ byte-identical output. Use `BTreeMap` (not
  `HashMap`) for anything serialized; no wall-clock times or random IDs in measurement output.
- **NaN/±∞ are invalid** canonical values — validate and reject them.
- **Confidence is validated**, bounded, and never silently promoted to certainty. Tracker
  confidence is algorithm-specific, not a calibrated cross-tracker probability.
- **Provenance travels with derived data**: implementation id, version, and config needed to
  reproduce it.
- **Domain logic lives in `openbar-core`.** CLI, tracking, and future UI/media layers consume it;
  they do not reimplement calibration, kinematics, or benchmark metrics.

## Schemas and versioning

- Persisted types use `#[serde(deny_unknown_fields)]`, a required integer `schema_version` (or
  `method_version` / `VERSION` constant), and stable `snake_case` enum strings. Readers fail
  closed on other versions.
- Changing serialized shape or semantics means: bump the relevant version constant, update the
  matching `validation/schema/*.schema.json` (the canonical `Analysis` is
  `analysis-v1.schema.json`), update the doc under `docs/data/` or `docs/validation/`, and update
  fixtures. `python validation/tools/schema_check.py` must stay green.
- `crates/openbar-core/tests/fixtures/analysis-v1.golden.json` is compared byte-for-byte by
  `analysis::tests::golden_json_is_stable`. Only regenerate it for an intentional, documented
  change — never to make a failing test pass.
- Any parameter that materially changes measurement output must be documented.

## Rust conventions

- Edition 2021. Dependencies are deliberately minimal (`serde`, `serde_json`). Adding any
  dependency — especially CV/ML — needs justification: source, licence, purpose, distribution
  compatibility (see ARCHITECTURE.md "Dependency policy"). Prefer none.
- Errors: hand-written enums implementing `Display` + `std::error::Error` per module
  (`CalibrationError`, `BenchmarkError`, `TrackerError`, …). No `thiserror`/`anyhow`.
  Constructors that validate are `try_new(...) -> Result<Self, _>`.
- Units are in field names: `_px`, `_m`, `_mm`, `_s`. Keep that suffix convention.
- Pixel coordinates are display-oriented, origin top-left, +X right, +Y down.
- Inline format args (`format!("{x}")`), collapse nested `if`s, keep clippy clean with
  `-D warnings`.
- Unit tests live in `#[cfg(test)] mod tests` in the same file; shared test helpers go in
  `crates/openbar-core/src/test_utils.rs`. Integration tests for tracking live in
  `crates/openbar-tracking/tests/`.
- Numeric logic needs deterministic tests, including error paths and edge cases (empty input,
  zero window, out-of-range confidence, non-finite values). Prefer comparing whole values over
  field-by-field asserts.

## Data, legal and clean-room rules

- Do not commit media, datasets, model weights, or `.onnx` files unless redistribution rights are
  documented in the fixture manifest. `*.mp4` etc. are git-ignored; public fixtures are
  force-added deliberately.
- Clean-room: never reverse engineer, decompile, or copy proprietary competitor code, models,
  assets, UI, or text. Implement from first principles or published literature. When referencing
  external code/data, cite source and licence. See `docs/clean-room/COMPETITOR_BOUNDARIES.md`.
- Licence is PolyForm Shield 1.0.0 (source-available, not OSI). Don't add files under a
  different licence or change licensing text.

## Scope discipline

M0 explicitly excludes: automatic plate detection, UI/Flutter, cloud/accounts, AI coaching, pose
estimation, live camera, BLE sensors, subscriptions. Don't introduce these. `analyze` is the real M0 measurement integration command. `render` is a diagnostic consumer of canonical analysis and must not reimplement or alter measurement logic. See `docs/validation/CLI_PIPELINE.md` and `docs/validation/DIAGNOSTIC_RENDERING.md`.

## Git

- Conventional commits with optional scope: `feat(core): …`, `fix(tracking): …`,
  `test(openbar-core): …`, `refactor(cli): …`, `docs: …`, `ci: …`.
- One focused change per PR. Reference the issue number when one exists.
- When adding or changing an API or contract, update the matching doc in `docs/` in the same
  change.
