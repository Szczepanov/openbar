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

Prerequisite: stable Rust.

```bash
cargo fmt --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
cargo run -p openbar-cli
```

## Working name

"OpenBar" is a working project/product name. Trademark and App Store name clearance should happen before branding investment or public launch.
