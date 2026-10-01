# OpenBar

OpenBar is a local-first barbell video-analysis project focused on reproducible measurement: track a plate from ordinary lifting video, calibrate the trajectory into physical units, derive kinematics, and make uncertainty visible.

> **Status:** pre-alpha / M0 foundation. There is no production app yet.

## Immediate goal: M0 — Validated Bar-Path Engine

Given a side-view video, a manually identified plate, and a known plate diameter, OpenBar should:

- track the same plate through the supported clip;
- output calibrated X/Y position in metres;
- derive velocity from a documented filtering pipeline;
- preserve raw measurements alongside derived values;
- report tracking/measurement confidence rather than silently inventing missing data;
- render/export the trajectory;
- pass deterministic golden-fixture tests and quantitative validation gates.

Automatic plate detection, Flutter UI, cloud services, AI coaching, real-time tracking, pose estimation, accounts, subscriptions, and coach dashboards are deliberately postponed until M0 is credible.

## Architecture

The intended split is:

- **Rust** — authoritative production core for calibration, trajectories, filtering, kinematics, rep/event logic, comparisons, confidence/provenance, and eventually production inference orchestration.
- **Python/PyTorch** — computer-vision research, dataset tooling, training, evaluation, notebooks, and model export.
- **ONNX** — intended model boundary between research/training and production inference.
- **Flutter** — intended mobile/desktop UI after the headless engine is validated.
- **Native platform media APIs** — preferred for hardware-accelerated decode/camera pipelines where appropriate.

The first executable surface is a headless CLI, not a mobile UI.

See [docs/architecture/ARCHITECTURE.md](docs/architecture/ARCHITECTURE.md).

## Repository layout

```text
apps/
  openbar-cli/
crates/
  openbar-core/
  openbar-tracking/
docs/
  adr/
  architecture/
  clean-room/
  data/
  legal/
  product/
  roadmap/
  validation/
ml/             # added when the first CV experiment is started
validation/     # fixtures/annotations added only with redistribution rights
```

## Validation data

M0 fixture metadata is versioned under `validation/`. Public fixture media may only be
committed when redistribution rights are documented; private/local research footage stays
outside Git. See [M0 fixture dataset and provenance](docs/validation/FIXTURE_DATASET.md).

## Product direction

The intended official app can be free for core athlete workflows while leaving room for optional paid features later, such as advanced longitudinal analytics, cloud sync, AI-assisted explanations, or coach/team workflows.

The free product should remain useful; the architecture should not depend on intentionally crippling local analysis.

## Clean-room rule

OpenBar is not intended to copy any application's implementation. Publicly documented product behaviour may inform requirements, but contributors must not reverse engineer proprietary code/models, copy proprietary assets/text/UI, or submit code derived from non-public implementations.

See [docs/clean-room/COMPETITOR_BOUNDARIES.md](docs/clean-room/COMPETITOR_BOUNDARIES.md).

## Licensing

This repository is **source-available**, not OSI open source.

The code is licensed under the [PolyForm Shield License 1.0.0](LICENSE.md), subject to the notices in [NOTICE](NOTICE). The licence permits broad use, modification, and redistribution but excludes use to provide a competing product.

This is an intentional starting position. Licensing, trademarks, contributor agreements, App Store distribution, and future commercial licensing should receive professional legal review before a public product launch.

See [docs/legal/LICENSING_STRATEGY.md](docs/legal/LICENSING_STRATEGY.md).

## Development

Prerequisites:

- **Rust 1.98.1** with clippy and rustfmt. `rust-toolchain.toml` pins it, so `rustup` installs
  the right toolchain on first use.
- **Python 3.14** for the validation tooling. Standard library only; no packages to install.
- **FFmpeg** (`ffmpeg` and `ffprobe` on `PATH`) for `analyze`, `tracker-run`, and the decode tests. OpenBar
  runs FFmpeg as a separate program and neither links nor ships it (ADR-0006). Without FFmpeg,
  the decode tests print `SKIPPED` locally. CI sets `OPENBAR_REQUIRE_FFMPEG=1`, so a missing
  install fails there. The frame source needs `-fps_mode` and `-enc_time_base demux`, so use a
  current FFmpeg release (developed with 9.0.2). Older builds fail with an explicit ffmpeg error.

CI runs these on Ubuntu (tests and smoke runs also on Windows). Run them before opening a PR:

```bash
cargo fmt --all -- --check
cargo clippy --locked --workspace --all-targets --all-features -- -D warnings
cargo test --locked --workspace --all-targets --all-features
cargo run --locked -p openbar-cli -- tracker-experiment --output target/tracker-experiment.json
cargo run --locked -p openbar-cli -- filter-experiment --output target/filter-experiment.json
cargo run --locked -p openbar-cli -- analyze --manifest validation/fixtures/public/manifest.json --fixture synthetic-clean-side-12 --seed validation/fixtures/public/seeds/synthetic-clean-side-12.manual-target-seed-v1.json --plate-diameter-m 0.45 --tracker template --filter raw --kinematics-max-gap-s 0.2 --kinematics-min-confidence 0 --output target/analyze-smoke.json
cargo run --locked -p openbar-cli -- benchmark --suite validation/benchmarks/synthetic-tracker-smoke.benchmark-v1.json --output target/benchmark-smoke.json
cargo run --locked -p openbar-cli -- tracker-run --manifest validation/fixtures/public/manifest.json --fixture synthetic-clean-side-12 --seed validation/fixtures/public/seeds/synthetic-clean-side-12.manual-target-seed-v1.json --output-dir target/tracker-run-smoke
python -m unittest discover -v -s validation/tests -p 'test_*.py'
python validation/tools/schema_check.py --schema validation/schema/analysis-v1.schema.json target/analyze-smoke.json
python validation/tools/schema_check.py
```

`analyze` is the canonical M0 integration command. It requires explicit tracker, filter and kinematics configuration and writes `analysis-v1` JSON. See [docs/validation/CLI_PIPELINE.md](docs/validation/CLI_PIPELINE.md). `render` is wired as a canonical-input boundary but remains intentionally unavailable until #13 implements diagnostics.

CI also runs [`cargo-deny`](https://github.com/EmbarkStudios/cargo-deny) against
[`deny.toml`](deny.toml) to check dependency licences, advisories and sources. To run it locally,
install it with `cargo install cargo-deny --locked`, then run `cargo deny --all-features --locked check`.

## Working name

"OpenBar" is a working project/product name. Trademark and App Store name clearance should happen before branding investment or public launch.
