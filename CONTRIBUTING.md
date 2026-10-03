# Contributing

OpenBar is currently in an architecture/validation bootstrap phase.

## Before contributing

- Read [VISION.md](VISION.md).
- Read the ADRs in [docs/adr](docs/adr).
- Follow the clean-room rules in [docs/clean-room/COMPETITOR_BOUNDARIES.md](docs/clean-room/COMPETITOR_BOUNDARIES.md).
- Do not contribute videos, datasets, model weights, text, UI assets, or code unless you have the right to redistribute them under the terms selected for that artifact.

## Contributor licensing

OpenBar is MIT-licensed. By submitting a contribution, you agree that it is licensed under the MIT License, the same licence as the project. No contributor agreement is required.

See [docs/legal/CONTRIBUTOR_LICENSING.md](docs/legal/CONTRIBUTOR_LICENSING.md).

## Engineering expectations

- Keep measurement logic in `openbar-core`, independent of UI.
- Preserve raw measurements when adding filtering/derived metrics.
- Add deterministic tests for numeric logic.
- Add golden fixtures for behavioural changes when redistribution rights permit.
- Do not silently interpolate long tracking gaps.
- Document any parameter that materially changes measurement output.
- Run the checks listed under [Development](README.md#development) before opening a PR. The PR
  template repeats them.

## Security issues

Report suspected vulnerabilities privately as described in [SECURITY.md](SECURITY.md), not in a
public issue.
