# Project-context pack

OpenBar sometimes needs a compact set of files uploaded into persistent AI/project-context systems.
Those exported files are **snapshots**, not a second documentation authority.

The Git repository remains canonical. In particular:

- accepted ADRs define durable architecture decisions;
- later accepted ADRs may supersede earlier ones;
- `README.md`, `VISION.md`, `AGENTS.md` and the focused documents under `docs/` describe the current state;
- historical decisions must not be copied into a generated context pack as if they were still current.

The drift that motivated this workflow was licensing: an older external context pack still described
OpenBar as PolyForm/source-available after ADR-0011 had changed the repository to MIT. The repository
itself was already correct. The fix is therefore to make the external pack reproducible from canonical
repository documents instead of manually maintaining duplicate summaries.

## Build

```bash
python scripts/build_project_context_pack.py \
  --output-dir target/project-context \
  --zip target/openbar-project-context.zip
```

By default the builder snapshots `HEAD`. To export another branch, tag or commit without checking it
out, pass `--ref <ref>`.

The ref is resolved to an immutable commit first. All exported content is then read from that Git
tree with `git show`; uncommitted working-tree edits are never mixed into a snapshot that claims to
come from the recorded commit.

The output directory may contain files from a previous generated pack, but it must not contain
unmanaged entries. This prevents obsolete or unrelated files from being silently uploaded with a
new snapshot.

The generated directory contains the familiar numbered context files plus a generated `README.md`.
Each file records the exact source commit and the canonical repository paths it embeds.

## Canonical mapping

| Exported file | Canonical repository sources |
| --- | --- |
| `00_PROJECT_CONTEXT.md` | `README.md`, `VISION.md` |
| `01_VISION_AND_PRODUCT.md` | `VISION.md`, `docs/product/PRODUCT_STRATEGY.md` |
| `02_ARCHITECTURE.md` | `docs/architecture/ARCHITECTURE.md` |
| `03_M0_ROADMAP.md` | `docs/roadmap/M0.md`, `docs/plans/M0_BAR_PATH_MEASUREMENT_EXPERIMENT_PLAN.md` |
| `04_VALIDATION_PROTOCOL.md` | `docs/validation/M0_VALIDATION.md`, `docs/validation/BENCHMARK.md` |
| `05_DOMAIN_MODEL.md` | `docs/data/ANALYSIS_SCHEMA.md` |
| `06_LICENSING_AND_IP.md` | `docs/legal/LICENSING_STRATEGY.md` |
| `07_CLEAN_ROOM_BOUNDARIES.md` | `docs/clean-room/COMPETITOR_BOUNDARIES.md` |
| `08_ENGINEERING_PRINCIPLES.md` | `VISION.md`, `AGENTS.md` |
| `09_AGENT_WORKFLOW.md` | `AGENTS.md`, `SUBAGENT_ROUTING.md` |
| `10_DECISIONS_LOG.md` | every committed Markdown ADR under `docs/adr/`, discovered dynamically |

`10_DECISIONS_LOG.md` contains both a generated status/supersession index and the full text of every
committed ADR. This keeps accepted decisions, superseded history and future ADRs in the pack without
hard-coding a particular ADR number as permanently current.

## Deterministic ZIP export

ZIP members are written in a fixed order with fixed timestamps, Unix creator metadata and file mode.
The archive deliberately uses stored (uncompressed) members rather than Deflate so byte identity does
not depend on the host platform or compression-library implementation. The context pack is small
enough that compression is not worth weakening reproducibility.

## Updating external project context

After a durable repository decision changes:

1. merge the canonical documentation/ADR change first;
2. regenerate the pack from the new `main` commit;
3. replace the external uploaded context files with the newly generated set;
4. never hand-edit the exported files as the primary way to change project policy.

This keeps external assistants useful without allowing an old snapshot to override current repository
truth.
