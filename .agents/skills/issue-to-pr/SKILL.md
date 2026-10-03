---
name: issue-to-pr
description: Implement a GitHub issue end-to-end from its number for OpenBar — read issue plus history, analyze docs and code, write a plan, implement it, verify docs and repo checks, and open a linked PR. Use when the user gives a GitHub issue number to implement.
---

# Issue to PR: GitHub Issue Implementation Workflow

You take a GitHub issue number as input and drive it to an opened PR: issue intake, docs/code analysis, detailed plan, implementation, docs verification, static checks, PR creation.

## Input

- Required: GitHub issue number `N` (accepts `123`, `#123`, or an issue URL — extract `N`).
- If `N` is missing or ambiguous, ask for it and stop. Do not guess which issue to implement.
- Optional overrides: base branch (default `main`), plan-approval gate (default on for high-risk changes, see Phase 4), draft PR vs ready PR.

## Phase 0 — Preconditions (do first, every time)

1. Read [AGENTS.md](file:///c:/Users/mdszc/Downloads/projekty/openbar/AGENTS.md) (system constraints, required test/smoke commands), [VISION.md](file:///c:/Users/mdszc/Downloads/projekty/openbar/VISION.md) (core measurement philosophy), [docs/plans/README.md](file:///c:/Users/mdszc/Downloads/projekty/openbar/docs/plans/README.md) (plan documentation hierarchy), [docs/architecture/ARCHITECTURE.md](file:///c:/Users/mdszc/Downloads/projekty/openbar/docs/architecture/ARCHITECTURE.md), and [CLAUDE.md](file:///c:/Users/mdszc/Downloads/projekty/openbar/CLAUDE.md) (large file conventions, golden file protections).
2. Confirm starting state of the main checkout (you will not work in it):
   - `git status --short --branch`
   - `git log --oneline -5`
   - `git worktree list` — check for an existing worktree/branch for this issue.
3. Create an isolated worktree from updated `main` — never implement in the main checkout:
   ```bash
   git fetch origin
   git worktree add ../openbar-issue-<N>-<short-slug> -b issue-<N>-<short-slug> origin/main
   ```
   - Sibling placement (`../openbar-issue-<N>-<short-slug>`) keeps the main checkout's `git status` clean.
   - If a worktree or the `issue-<N>-*` branch already exists and is yours and clean, reuse it. If it belongs to someone else or is dirty, stop and ask.
   - If the session already runs inside a clean harness-created worktree (e.g. a `claude/...` branch made by the desktop app), use that as `WORKTREE` instead of creating a sibling; do not nest worktrees.
   - In PowerShell, create with equivalent syntax; use forward slashes in arguments passed to CLI tools.
   - Record the worktree path as `WORKTREE`. Every later phase runs there: shell commands via workdir, file tools via absolute paths under `WORKTREE`.
   - Fresh worktrees share cargo cache / target if configured; Python validation tools use stdlib only (Python 3.14, no third-party packages to install).
4. Rules for the whole run:
   - **Measurement non-negotiables (VISION.md & ADR-0003/ADR-0005):**
     - **Raw is never replaced.** Filtered/derived values sit alongside raw observations, never overwrite them.
     - **Lost means no coordinate.** A lost/untracked sample carries no fake position or bounding box. Do not silently interpolate gaps.
     - **Timestamps are authoritative**, not frame index or nominal FPS.
     - **Determinism.** Same inputs + pipeline version ⇒ byte-identical output. Use `BTreeMap` (not `HashMap`) for anything serialized; no wall-clock times or random IDs in measurement output.
     - **NaN/±∞ are invalid** canonical values — validate and reject them.
     - **Confidence is validated**, bounded in `[0.0, 1.0]`, and never silently promoted to certainty. Tracker confidence is algorithm-specific.
     - **Provenance travels with derived data**: implementation id, version, and config needed to reproduce it.
     - **Domain logic lives in `crates/openbar-core`.** CLI, tracking, and future UI/media layers consume it; they do not reimplement calibration, kinematics, or benchmark metrics.
     - **Pixel coordinates:** display-oriented, +X right, +Y down, integer `(i, j)` at pixel centres; `(0, 0)` is the centre of the top-left pixel (ADR-0007). `[0, width)` / `[0, height)` for points; `[0, width]` / `[0, height]` for bounds.
     - **Units in field names:** `_px`, `_m`, `_mm`, `_s`.
   - **Data, legal and clean-room rules:**
     - Licence is MIT. Do not add code, data or models under licences incompatible with MIT, and do not change licensing text unless the owner asks.
     - Clean-room discipline ([docs/clean-room/COMPETITOR_BOUNDARIES.md](file:///c:/Users/mdszc/Downloads/projekty/openbar/docs/clean-room/COMPETITOR_BOUNDARIES.md)): never reverse-engineer or copy proprietary competitor code, models, assets, UI, or text.
     - Never commit media (`*.mp4`, etc.), datasets, model weights, or `.onnx` files unless redistribution rights are documented in the fixture manifest. Public fixtures are force-added intentionally; private media belongs in `validation/private/` (git-ignored).
     - Never commit credentials, secrets, or API tokens.
   - **Scope discipline:**
     - M0 explicitly excludes: automatic plate detection, UI/Flutter, cloud/accounts, AI coaching, pose estimation, live camera, BLE sensors. Do not scaffold `ml/` or Flutter crates.
   - Reference symbols, never line numbers, in docs and plans.
   - Work in `WORKTREE` on its feature branch, never directly on `main`. One issue = one worktree = one branch.
   - For a single cohesive issue, keep issue discovery, planning, implementation, and deterministic verification in the primary agent. Do not spawn research subagents merely to repeat repository discovery. Use one diff-first review after implementation.

## Phase 1 — Read the GitHub issue and its history

Fetch, do not paraphrase from memory:

```bash
gh issue view <N> --json number,title,body,state,labels,assignees,milestone,url,createdAt,updatedAt,author
gh issue view <N> --comments
```

Then check for prior/related work so you do not duplicate or contradict it:

- `gh pr list --limit 100 --json number,title,state,headRefName,baseRefName,body --search "#<N>"` (PRs mentioning the issue).
- `git log --oneline --grep="#<N>" -20` and `git log --oneline --grep="<N>" -20`.
- `git branch -a | grep -i "<N>"` for existing issue branches.

Summarize back, briefly:

- Issue title, state (open/closed), labels, assignee.
- The actual request in 2–4 bullets (problem, desired behavior, acceptance criteria if stated).
- History: key comment decisions, scope changes, rejected approaches, linked PRs/issues.
- For long threads (more than ~30 comments), read the body, the latest ~15 comments and any maintainer decisions in full; skim the rest for scope changes instead of pasting everything into context.
- Ambiguities or missing acceptance criteria. If the issue is ambiguous about scope or success criteria, ask before coding.

## Phase 2 — Analyze the docs (routing matters)

First check [docs/plans/README.md](file:///c:/Users/mdszc/Downloads/projekty/openbar/docs/plans/README.md) and active plans in `docs/plans/`: the issue may already be planned, in progress under another plan, or deliberately out-of-scope for M0. Never infer delivery status from a file's existence alone.

Follow the repository documentation hierarchy: **code wins for current behavior, then accepted ADRs (`docs/adr/`), then normative validation contracts (`docs/validation/`), then `docs/architecture/`, then `docs/data/` and `docs/plans/`.**

1. Map the issue to its domain and entry point:
   - **Canonical Analysis & domain aggregate:** ADR-0003, [docs/data/ANALYSIS_SCHEMA.md](file:///c:/Users/mdszc/Downloads/projekty/openbar/docs/data/ANALYSIS_SCHEMA.md), `validation/schema/analysis-v1.schema.json`.
   - **Tracker frame boundary & algorithms:** ADR-0005, [docs/validation/TRACKER_EXPERIMENTS.md](file:///c:/Users/mdszc/Downloads/projekty/openbar/docs/validation/TRACKER_EXPERIMENTS.md), [docs/validation/TRACKER_FILTER_SELECTION.md](file:///c:/Users/mdszc/Downloads/projekty/openbar/docs/validation/TRACKER_FILTER_SELECTION.md).
   - **Video decode boundary:** ADR-0006, `apps/openbar-cli/src/media/`.
   - **Pixel coordinates & geometry:** ADR-0007, `crates/openbar-core/src/math.rs`.
   - **Plate calibration & target seeding:** [docs/validation/PLATE_CALIBRATION.md](file:///c:/Users/mdszc/Downloads/projekty/openbar/docs/validation/PLATE_CALIBRATION.md), [docs/validation/MANUAL_TARGET_SEED.md](file:///c:/Users/mdszc/Downloads/projekty/openbar/docs/validation/MANUAL_TARGET_SEED.md), [docs/plans/PLATE_GEOMETRY_REFINEMENT_PLAN.md](file:///c:/Users/mdszc/Downloads/projekty/openbar/docs/plans/PLATE_GEOMETRY_REFINEMENT_PLAN.md).
   - **Kinematics & trajectory filtering:** [docs/validation/KINEMATIC_METRICS.md](file:///c:/Users/mdszc/Downloads/projekty/openbar/docs/validation/KINEMATIC_METRICS.md), [docs/validation/FILTER_EXPERIMENTS.md](file:///c:/Users/mdszc/Downloads/projekty/openbar/docs/validation/FILTER_EXPERIMENTS.md).
   - **Benchmark suite & metric semantics:** ADR-0004, [docs/validation/BENCHMARK.md](file:///c:/Users/mdszc/Downloads/projekty/openbar/docs/validation/BENCHMARK.md), `validation/benchmarks/`.
   - **CLI subcommands & diagnostic render:** [docs/validation/CLI_PIPELINE.md](file:///c:/Users/mdszc/Downloads/projekty/openbar/docs/validation/CLI_PIPELINE.md), [docs/validation/DIAGNOSTIC_RENDERING.md](file:///c:/Users/mdszc/Downloads/projekty/openbar/docs/validation/DIAGNOSTIC_RENDERING.md).
   - **Validation tooling & schemas:** `validation/tools/`, `validation/schema/`, `validation/tests/`.
   - **Clean-room competitor boundaries:** [docs/clean-room/COMPETITOR_BOUNDARIES.md](file:///c:/Users/mdszc/Downloads/projekty/openbar/docs/clean-room/COMPETITOR_BOUNDARIES.md).
2. Record which docs describe intended design (ADRs/contracts) vs current implementation. Note any doc↔code disagreement explicitly instead of silently picking one.

## Phase 3 — Analyze the code and related artifacts

- Locate affected modules:
  - `crates/openbar-core`: domain models (`analysis.rs`), math (`math.rs`), calibration (`calibration.rs`), manual seeding (`manual_seed.rs`), filtering (`filtering.rs`), kinematics (`kinematics.rs`), benchmark metrics (`benchmark.rs`), recording support (`recording_support.rs`).
  - `crates/openbar-tracking`: tracking algorithms (`template.rs`, `contrast.rs`) behind `GrayscaleImage` / `FrameSample` boundary.
  - `apps/openbar-cli`: CLI dispatch, argument parsing, FFmpeg decode process (`src/media/`), subcommands (`analyze`, `benchmark`, `render`, `tracker-run`, `tracker-experiment`, `filter-experiment`).
  - `validation/`: schema definitions, public/private fixtures, stdlib Python validation tools (`annotations.py`, `schema_check.py`, `m0_evidence.py`).
- Code discovery:
  - Prefer exact lexical search (`rg`) for known symbols, error types, field names, and paths.
  - If discovery bridges exist (`scripts/agent_canopy.py` / `scripts/agent_jev.py`), follow repository rules or fall back immediately to `rg` upon exit code 3.
- When correctness depends on an external crate/API contract (e.g. `serde` attributes, `criterion`, FFmpeg CLI flags), use Context7 for current documentation. Do not use Context7 for repository-internal domain behavior.
- **Handling large files:**
  - Files such as `analysis.rs`, `calibration.rs`, `manual_seed.rs`, and `apps/openbar-cli/src/benchmark.rs` are substantial. View targeted line ranges with start/end bounds or grep for specific symbols instead of dumping entire files.
- **Dependencies & errors:**
  - Dependencies are intentionally minimal. Adding any dependency requires justification and `deny.toml` check.
  - Errors are hand-written enums implementing `Display` + `std::error::Error` (`CalibrationError`, `BenchmarkError`, `TrackerError`, etc.). No `thiserror` or `anyhow`.
- **Contracts and golden files:**
  - Serialized types use `#[serde(deny_unknown_fields)]` and integer `schema_version` / `method_version` / `VERSION` constants.
  - Golden file `crates/openbar-core/tests/fixtures/analysis-v1.golden.json` is byte-for-byte compared. Only regenerate it for an intentional, documented versioned change.

## Phase 4 — Create the detailed implementation plan

Write the plan out (in chat; only create a file under `docs/plans/` if creating a durable, multi-step engineering plan). Structure:

```markdown
# Implementation Plan: #<N> <short title>
## Goal (2–3 sentences + success criteria)
## Scope (in / out — respect M0 boundaries)
## Docs & code findings (ADR/validation doc refs, symbols, current behavior)
## Steps (ordered: contracts/domain models → core logic → tracking/CLI → tests → schemas/docs)
## Tests (new/updated unit tests, golden tests, smoke commands)
## Docs & schema updates required (version bumps, schema updates, schema_check.py catalogue)
## Risks & mitigations (numerical instability, determinism regressions, contract breakages)
```

- Order steps: contracts/domain models → core logic → tracking/CLI adapters → tests → docs.
- Each step must leave the tree compiling and passing tests.
- **Approval gate:** present the plan and wait for approval when the change:
  (a) Alters measurement/calibration/kinematic equations or threshold semantics,
  (b) Modifies serialized JSON contracts (`analysis-v1`, etc.), `*_VERSION` constants, or golden files,
  (c) Touches video decoding or tracker frame boundaries,
  (d) Modifies dependencies or `deny.toml`, or
  (e) The issue requirements were ambiguous.
  For small, well-specified fixes or test additions, state the plan concisely and proceed.

## Phase 5 — Implement the plan

1. Worktree and branch already exist from Phase 0 — verify before touching code:
   - `git -C WORKTREE status --short --branch` shows `issue-<N>-<short-slug>` on top of recent `origin/main`.
   - Never implement in the main checkout silently.
2. Implement step by step, keeping diffs minimal and following idiomatic Rust 2021 conventions (`format!("{x}")`, clippy `-D warnings` clean).
3. Unit tests live in `#[cfg(test)] mod tests` in the touched file; tracking integration tests go in `crates/openbar-tracking/tests/`; CLI integration tests go in `apps/openbar-cli/tests/`; Python validation tests go in `validation/tests/`.
4. Commit in logical increments with conventional commit messages referencing the issue:
   - `feat(core): ... (#<N>)`
   - `fix(tracking): ... (#<N>)`
   - `test(cli): ... (#<N>)`
   - `docs(validation): ... (#<N>)`
   Never commit unapproved media, `.onnx` files, secrets, or competitor code.

## Phase 6 — Verify related docs and contracts were updated properly

Before running checks, confirm:

- [ ] If serialized shape or semantics changed: version constant bumped, `validation/schema/*.schema.json` updated, matching doc under `docs/data/` or `docs/validation/` updated, `python validation/tools/schema_check.py` passes, and golden file regenerated intentionally if applicable.
- [ ] If measurement, calibration, kinematic, or filtering behavior changed: relevant contract under `docs/validation/` updated.
- [ ] If architectural boundary changed: matching ADR under `docs/adr/` updated or created.
- [ ] If a new fixture, benchmark suite, or seed was added: registered in manifest, `schema_check.py` catalogue updated, and public redistribution rights documented.
- [ ] No line-number references added to docs; symbol names used instead.
- [ ] Clean-room compliance: no competitor code, models, assets, or non-public details copied ([docs/clean-room/COMPETITOR_BOUNDARIES.md](file:///c:/Users/mdszc/Downloads/projekty/openbar/docs/clean-room/COMPETITOR_BOUNDARIES.md)).
- [ ] Any doc↔code divergence found was either reconciled in docs or explicitly noted.

## Phase 7 — Static analysis and tests with repository tools

All commands run in `WORKTREE` (shell workdir / `git -C WORKTREE`).

The required repository gates (from CI and [AGENTS.md](file:///c:/Users/mdszc/Downloads/projekty/openbar/AGENTS.md)):

```bash
# 1. Rust code formatting and lints
cargo fmt --all -- --check
cargo clippy --locked --workspace --all-targets --all-features -- -D warnings
cargo build --locked --workspace --all-targets --all-features
cargo test --locked --workspace --all-targets --all-features

# 2. Scope-specific smoke commands (run applicable subset)
cargo run --locked -p openbar-cli -- tracker-experiment --output target/tracker-experiment.json
cargo run --locked -p openbar-cli -- filter-experiment --output target/filter-experiment.json
cargo run --locked -p openbar-cli -- tracker-run --manifest validation/fixtures/public/manifest.json --fixture synthetic-clean-side-12 --seed validation/fixtures/public/seeds/synthetic-clean-side-12.manual-target-seed-v1.json --output-dir target/tracker-run-smoke
cargo run --locked -p openbar-cli -- analyze --manifest validation/fixtures/public/manifest.json --fixture synthetic-clean-side-12 --seed validation/fixtures/public/seeds/synthetic-clean-side-12.manual-target-seed-v1.json --plate-diameter-m 0.45 --tracker template --filter raw --kinematics-max-gap-s 0.2 --kinematics-min-confidence 0 --output target/analyze-smoke.json
cargo run --locked -p openbar-cli -- benchmark --suite validation/benchmarks/synthetic-tracker-smoke.benchmark-v1.json --output target/benchmark-smoke.json

# 3. Python validation tooling (stdlib only)
python -m unittest discover -v -s validation/tests -p 'test_*.py'
python validation/tools/schema_check.py

# 4. Dependency and licensing gate (if cargo-deny is installed)
cargo deny --all-features --locked check
```

- Note: `tracker-run` and FFmpeg decode tests require `ffmpeg` and `ffprobe` on `PATH`. In local dev without FFmpeg, tests print `SKIPPED`; CI fails if missing.
- Record exact commands plus pass/fail results for the PR body. If an applicable check is skipped, state why — never claim CI will cover it.
- Fix failures in place; re-run the affected scope until green. Do not open a PR on red checks without explicit user instruction.

## Phase 7.5 — Independent review (before the PR)

Do not rely only on the implementer's own inspection, but do not pay for a second full repository analysis.

- For non-trivial code changes, run **one** read-only, diff-first review (using `code-reviewer` subagent if available). Give it the issue acceptance criteria, implementation summary, changed-file list, and branch diff.
- The reviewer must focus on:
  - OpenBar domain invariants: raw preserved, lost explicit (no silent coordinate interpolation), timestamps authoritative, determinism (`BTreeMap`, no random/wall-clock values).
  - Numerical edge cases: zero division, non-finite values (NaN/±∞ validation), empty inputs, coordinate boundary clamping.
  - License and clean-room constraints.
- Address CRITICAL/HIGH findings.
- Skip independent review for trivial docs-only changes, and say so in the PR.

## Phase 8 — Create the PR with detailed description and linked issue

1. Push the branch from the worktree: `git -C WORKTREE push -u origin issue-<N>-<short-slug>`.
2. Verify what the PR will contain: `git -C WORKTREE status`, `git -C WORKTREE diff --stat origin/main...HEAD`, `git -C WORKTREE log --oneline origin/main..HEAD`.
3. Create the PR matching [.github/pull_request_template.md](file:///c:/Users/mdszc/Downloads/projekty/openbar/.github/pull_request_template.md) (use `--draft` only if checks are incomplete or requested):
   ```bash
   gh pr create --base main --head issue-<N>-<short-slug> \
     --title "<type>(<scope>): <behavior change> (#<N>)" \
     --body "$(cat <<'EOF'
   ## Summary
   - What changed and why?
   - Closes #<N>.
   
   ## Linked work and scope
   - Related issues / ADRs / validation docs:
   - Out of scope / follow-up:
   
   ## Measurement and contract impact
   - [ ] No change to measurement output, serialized contracts, schema/version constants, or benchmark semantics.
   - [ ] Measurement behaviour changes and deterministic regression evidence is included.
   - [ ] Serialized shape/semantics changes: the relevant version was bumped and schemas/docs/fixtures were updated.
   - [ ] Public fixtures, annotations, seeds, predictions, or golden files change intentionally and provenance/redistribution rights are documented.
   - [ ] A dependency changes: source, licence, purpose, distribution compatibility, and `deny.toml` impact were reviewed.
   
   Details:
   
   ## Validation
   - [x] `cargo fmt --all -- --check`
   - [x] `cargo clippy --locked --workspace --all-targets --all-features -- -D warnings`
   - [x] `cargo build --locked --workspace --all-targets --all-features`
   - [x] `cargo test --locked --workspace --all-targets --all-features`
   - [x] `python -m unittest discover -v -s validation/tests -p 'test_*.py'`
   - [x] `python validation/tools/schema_check.py`
   - [x] Scope-specific tracker/filter/analyze/benchmark/render/evidence checks completed, or not applicable.
   
   Results:
   ```text
   <command>: pass — <coverage summary>
   ```
   
   ## Risk and reviewer guidance
   - Highest-risk seam, plausible regression, compatibility concern, rollback path.
   
   ## Architecture and M0 invariants
   - [x] Raw observations remain preserved; derived/filtered values do not overwrite them.
   - [x] Missing/lost/low-confidence tracking remains explicit; no fabricated coordinates or silent long-gap interpolation.
   - [x] Timestamps remain authoritative for time-dependent logic; units are explicit.
   - [x] Same input + pipeline version remains reproducible/deterministic.
   - [x] Authoritative domain logic stays in `openbar-core`; CLI/tracking/validation layers do not duplicate it.
   - [x] Durable architecture/behaviour changes are reflected in ADRs/docs.
   - [x] Change stays inside current M0 scope, or the scope decision is explicitly reopened and documented.
   
   ## Data, licensing, and clean-room review
   - [x] No credentials, secrets, private user data, or unapproved media/data/model artifacts are committed.
   - [x] Any external code/data/model/material referenced by this PR has source and licence recorded, with compatibility reviewed.
   - [x] Competitor behaviour may inform requirements only; no proprietary implementation, assets, text, models, datasets, or non-public details were copied.
   - [x] Contributor-rights requirements in `CONTRIBUTING.md` are satisfied before merge.
   
   ## Evidence / artifacts
   - Benchmark outputs, diagnostic renders, smoke test outputs, or "Not applicable — <reason>".
   EOF
   )"
   ```
4. Always include `Closes #<N>` so the issue links and closes automatically on merge.
5. Return the PR URL plus a one-paragraph summary: readiness state, which checks passed, and any follow-ups.
6. Cleanup: leave the worktree until the PR is merged. After merge:
   - `git worktree remove ../openbar-issue-<N>-<short-slug>`
   - `git worktree prune`

## Stop conditions — ask instead of guessing

- Issue number missing, ambiguous, or closed with no reopen instruction.
- A worktree or `issue-<N>-*` branch already exists that is not yours, or is dirty.
- The issue asks for behavior that violates non-negotiable measurement invariants (e.g. overwriting raw data, inventing points across gaps, non-deterministic outputs).
- The issue requires adding new third-party dependencies with unclear licensing or incompatible with `deny.toml`.
- The change alters accepted ADRs or golden files (`analysis-v1.golden.json`) without explicit maintainer intent.
- Acceptance criteria are absent and the correct mathematical or product behavior requires an architectural decision.
