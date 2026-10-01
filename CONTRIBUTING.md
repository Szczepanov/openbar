# Contributing

OpenBar is currently in an architecture/validation bootstrap phase.

## Before contributing

- Read [VISION.md](VISION.md).
- Read the ADRs in [docs/adr](docs/adr).
- Follow the clean-room rules in [docs/clean-room/COMPETITOR_BOUNDARIES.md](docs/clean-room/COMPETITOR_BOUNDARIES.md).
- Do not contribute videos, datasets, model weights, text, UI assets, or code unless you have the right to redistribute them under the terms selected for that artifact.

## Contributor licensing

External contributions should **not be merged until the project has adopted a professionally reviewed contributor agreement or equivalent rights-management process**. This is intentional: the project may need future dual/commercial licensing flexibility.

See [docs/legal/CONTRIBUTOR_LICENSING.md](docs/legal/CONTRIBUTOR_LICENSING.md).

## Engineering expectations

- Keep measurement logic in `openbar-core`, independent of UI.
- Preserve raw measurements when adding filtering/derived metrics.
- Add deterministic tests for numeric logic.
- Add golden fixtures for behavioural changes when redistribution rights permit.
- Do not silently interpolate long tracking gaps.
- Document any parameter that materially changes measurement output.
- Run `cargo fmt`, `cargo clippy`, and `cargo test` before opening a PR.
