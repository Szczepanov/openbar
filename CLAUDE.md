# CLAUDE.md

@AGENTS.md

## Claude Code specifics

The shared rules above are canonical. Edit `AGENTS.md`, not this file, when a rule applies to
every agent. Keep only Claude-specific notes here.

- **Environment.** The primary dev machine is Windows; CI is Ubuntu, plus a Windows job that runs
  the workspace tests and both smoke commands. Commands in AGENTS.md work in Git Bash and
  PowerShell. Use forward slashes in paths passed to the CLI.
- **Verification before "done".** Run fmt, clippy (`-D warnings`) and the scoped `cargo test -p
  <crate>` for every crate you touched. If you changed `openbar-core`, also run the full
  workspace test and both CLI smoke commands, since `openbar-tracking` and `openbar-cli` depend
  on it. Report the commands and their results; don't claim a pass you didn't observe.
- **Golden and fixture changes need a stop.** If a change alters `analysis-v1.golden.json`, a
  JSON schema, a `*_VERSION` constant, or anything under `validation/fixtures/`, call it out
  explicitly in your summary and say why it's intentional.
- **Read before changing a boundary.** Before touching serialization, the tracker frame boundary,
  or calibration semantics, read the matching ADR in `docs/adr/` and the contract in
  `docs/validation/` or `docs/data/`.
- **Large files.** `analysis.rs`, `calibration.rs`, `manual_seed.rs` and `apps/openbar-cli/src/benchmark.rs`
  are long. Read the region you need with offset/limit instead of the whole file.
- **No speculative scaffolding.** Don't create `ml/`, Flutter, inference, or video crates, or add
  dependencies, without being asked.
