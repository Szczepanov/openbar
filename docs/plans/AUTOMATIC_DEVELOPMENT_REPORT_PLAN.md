# Automatic development report

## Goal and boundaries

Run the existing `experiment.diagnose` pipeline in one batch from retained baseline snapshots,
using existing owner-confirmed seeds, manual annotations and SAM/CSRT predictions. No new human
work, automatic seeding, pseudo-annotations, detector, tracker tuning or held-out processing.
Sparse existing labels remain sparse evidence; no dense repeatability or physical accuracy claim.

## Implementation and checks

1. Add a stdlib batch wrapper validating snapshot identity/version and every consumed input hash
   before any report is written. Reject duplicate/held-out fixtures and existing output directories.
   Claim a new destination only after preflight and remove that invocation's partial destination if
   later diagnostics or delegated CLI work fails, so a corrected retry never requires manual cleanup.
2. Delegate metrics to `experiment.diagnose`; optional canonical mode delegates measurement to
   the existing Rust benchmark/analyze commands and rendering to the diagnostic CLI.
3. Produce a batch summary and per-clip reports, retain provenance, separate candidate streams,
   raw/lost samples and authoritative timestamps. Do not select a production candidate.
4. Test refusal paths, failed-run cleanup/retry, delegation and deterministic summaries. Run on the
   three labelled development clips; verify canonical outputs and repeat pure diagnostics into a
   separate private directory.
5. Deliver retained results and a replay command to the main project's private directory.

No new dependencies, canonical contracts or golden files. Run affected research tests, Python
validation/schema checks and diff check. Stop after automation of existing evidence. Human-free
operation does not satisfy independent-reference or held-out freeze prerequisites.
