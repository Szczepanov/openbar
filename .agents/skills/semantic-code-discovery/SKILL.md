---
name: semantic-code-discovery
description: Route repository discovery between exact lexical search (rg), local Canopy semantic retrieval/graph hints, and optional Jev semantic judgment while minimizing agent context in OpenBar. Use for source-code navigation, semantic audits, impact discovery, and locating domain logic.
---

# Semantic Code Discovery

Use the cheapest evidence source that answers the question. Prefer exact lexical search (`rg`) for known symbols, strings, errors, and paths; use semantic retrieval only for a genuine vocabulary gap. Never block work on optional tooling.

`canopy` means the local code index when maintained; `jev` means the code-navigation CLI backed by TypeSafe Jev. Both are optional: exit code 3 / `CANOPY_UNAVAILABLE` or `JEV_UNAVAILABLE` means fall back to `rg` immediately. Never install, bootstrap, or reindex either tool during a task.

## Routing

1. **Exact vocabulary known** — use `rg` (ripgrep) and direct file reads for symbols, structs, traits, errors, paths, and exact callers. Do not use semantic retrieval to rediscover a known identifier.
2. **Behavior known, repository vocabulary/location unknown** — for a non-trivial task, make one query-only discovery attempt with `python scripts/agent_canopy.py search "<behavior>"` **before a broad lexical sweep**.
   - If the script is missing or reports exit code 3 (`CANOPY_UNAVAILABLE`), fall back immediately to `rg`.
   - Treat returned files and chunks as candidates, not proof.
   - If the result is materially ambiguous, use one tightly scoped `python scripts/agent_jev.py find` as a second opinion.
3. **Known target, semantic property unknown** — use a targeted direct read when inspecting a small region answers the question. If one semantic property would otherwise require reading multiple substantial files, use **one atomic `python scripts/agent_jev.py ask`** against the implementation file or module.
4. **Call-graph orientation** — after a useful Canopy search hit, `canopy map` or `canopy trace` may provide advisory local graph context. If a symbol is not resolved, verify with `rg` rather than inferring absence.
5. **Type/signature ripple** — use the compiler (`cargo check --workspace --all-targets`) as the authoritative impact list.
6. **External library/API behavior** — use Context7 for third-party libraries (e.g. `serde`, `ffmpeg` CLI args, `criterion`). Never use Context7 for OpenBar-internal domain behavior.

## Subsystem Navigation Guide (OpenBar)

- **Domain Logic & Aggregates:** `crates/openbar-core/src/`
  - `analysis.rs`: Canonical `Analysis` aggregate, observation sets, serialization contracts.
  - `calibration.rs`: Metric plate calibration, scale factor derivation, pixel-to-metre ratios.
  - `kinematics.rs`: Velocity, acceleration, displacement calculations, gap filtering.
  - `filtering.rs`: Raw observation filters, Savitzky-Golay, Butterworth, moving averages.
  - `manual_seed.rs`: Manual target initialization bounds and plate radius seeds.
  - `math.rs`: Pixel coordinate transforms, bounding boxes, circle/ellipse geometry.
  - `benchmark.rs`: Metric semantics, ground-truth comparison algorithms.
  - `recording_support.rs`: Recording envelope and evidence boundaries.
- **Tracking Algorithms:** `crates/openbar-tracking/src/`
  - `template.rs`: Template-matching tracking implementations.
  - `contrast.rs`: Contrast/edge-based plate tracking.
  - Frame sample boundary: `FrameSample`, `GrayscaleImage`.
- **CLI & Media Execution:** `apps/openbar-cli/src/`
  - `media/`: ADR-0006 external FFmpeg/FFprobe subprocess video decoders.
  - Subcommands: `analyze`, `benchmark`, `render`, `tracker-run`, `tracker-experiment`, `filter-experiment`.
- **Validation & Schemas:** `validation/`
  - `validation/schema/`: JSON Schema definitions (`analysis-v1`, `annotation-v1`, etc.).
  - `validation/fixtures/public/`: Public test fixtures and `manifest.json`.
  - `validation/tools/`: Stdlib Python validation tools (`annotations.py`, `schema_check.py`, `m0_evidence.py`).

## Data Boundaries and Security

Jev sends selected source content to an external service. Treat Jev scopes as data egress:
- Never run Jev or Canopy over the repository root (`.`).
- Scope to explicit crates or subdirectories: `crates/openbar-core/`, `crates/openbar-tracking/`, `apps/openbar-cli/src/`.
- **Never scan or pass media files:** `*.mp4`, `*.mov`, `validation/private/**`, test videos, or `.onnx` files.
- Never scan secrets, `.env*`, or credentials.
- When in doubt, fall back to local lexical search (`rg`).

## Agent Economy

The primary agent owns broad discovery. Do not delegate duplicate discovery sweeps to subagents. Reviewers should be given the established files, symbols, and diffs, querying further only for concrete, unresolved questions.
