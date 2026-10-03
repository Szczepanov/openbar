# ADR-0011: Relicense OpenBar under the MIT License

- Status: Accepted
- Date: 2026-10-03
- Supersedes: [ADR-0002](0002-source-available-licensing.md)

## Context

ADR-0002 started the repository under PolyForm Shield 1.0.0 to discourage a third party from
cloning OpenBar and shipping it as a competing product.

In practice, the owner would neither detect nor litigate such a breach. Litigation is expensive,
slow and unlikely to succeed for a project of this size. The restriction therefore protects very
little while costing a lot: OpenBar cannot be called open source, permissive projects and users
are wary of it, and it needs a contributor agreement before any outside contribution can be
merged.

Every commit up to this decision was authored by the owner, so relicensing needs no other
contributor's consent.

## Decision

From this commit on, license all OpenBar code, documentation and repository-generated data under
the MIT License (`LICENSE`).

- Workspace crates declare `license = "MIT"`.
- Repository-generated fixtures, such as the synthetic public fixture, are MIT as well. Private
  fixtures under `validation/private/` stay unpublished and are not covered.
- Contributions are accepted under the same licence (inbound = outbound). No CLA is required.
- The `NOTICE` file, which carried PolyForm-specific terms, is removed. The copyright line now
  lives in `LICENSE`.

## Consequences

- Anyone may use, modify and redistribute OpenBar, including in closed-source and competing
  products, provided they keep the copyright and licence notice.
- Versions published under PolyForm Shield remain available under that licence to whoever
  received them. Because MIT is more permissive, this has no practical effect.
- The OpenBar name and any official app identity are not covered by the licence. Protect them
  through trademark and store listings if that ever matters.
- Future paid features remain possible, but they would have to live in code that isn't published
  in this repository.
- Third-party licences are unchanged. The dependency allow-list in `deny.toml` still applies. Code
  or models under copyleft or non-commercial terms (for example AGPL-3.0 Ultralytics, or
  CC-BY-NC CoTracker3 weights) must not be merged into the MIT codebase or shipped with it.

## Alternatives considered

- **MIT OR Apache-2.0 dual licence**: the Rust ecosystem convention, which adds an explicit patent
  grant. Not chosen because the owner preferred a single, short licence. Revisit it if patent
  terms start to matter.
- **Keep PolyForm Shield**: rejected for the reasons in Context.
- **GPL/AGPL**: copyleft adds obligations the owner has no plan to enforce.
