# Local VBT recovery and handoff implementation plan (#114)

**Goal:** Recover interrupted human-confirmed sessions and publish a repeatable,
research-only selection of unchanged canonical analyses with report and assessments.
Spec: [#114](https://github.com/Szczepanov/openbar/issues/114).

**Architecture:** Reuse `session_run.command_run`, the final session record,
workflow-v3 run hashes and #111 assessments. The recommender owns parsing and
shared segmentation; OpenBar validates outgoing evidence and never writes its database.
Tech stack: existing Python standard library orchestration, no new dependencies.

## Scope and findings

- `session_run.prepare` currently refuses partial outputs without `--force`.
- `watch_for_csv` and `time_session.wait_for_video` currently check only size;
  the latter silently selects the first of several arrivals.
- The consumer browser importer has no nonwriting CLI. Its existing preview uses
  `parseOpenBarAnalysis` and `CONCENTRIC_SEGMENTATION_V2`.
- Consumer main `21ae95e4cb8c9d1fed53216b2bc9bbe083b3d572` rejects nonempty
  tracker parameters, including retained OpenBar prediction hashes. Do not remove
  provenance to bypass this. Document the consumer compatibility gate.
- No canonical schema, measurement, decoder, tracker, golden, dependency or
  source-selection changes. Machine-origin sessions remain research-only and
  cannot use the human-confirmed handoff.

## Ordered work and checks

- [x] Add failing regressions in `research/vbt-workflow/tests/test_session_handoff.py`
  and arrival tests: interrupted run, absent/malformed final record, changed bound
  bytes, duplicate video, multiple tracker analyses, changing equal-size files,
  ambiguous arrivals, repeated resume and dry-run/package publication.
- [x] Add `session_handoff.completed_session` for read-only final-record, input,
  run, output and identity/hash validation, shared by `status`, resume and handoff.
- [x] Add `run --resume`: validate same inputs/configuration; completed runs are
  no-ops, interrupted runs use the existing rerun path. Preserve `--force` behavior.
- [x] Strengthen bounded arrival polling. Batch ingest permits distinct clips,
  deduplicates identical bytes and waits for a stable snapshot before copying.
- [x] Add `handoff` with explicit research tracker policy, bound retained #111
  assessments, outgoing validation dry run, and staged/idempotent directory publication.
  One file per confirmed source video; reject ambiguity, mismatches and missing evidence.
- [x] Update `docs/validation/VBT_LOCAL_HANDOFF.md` and workflow step 3 with
  operator commands, completion semantics and inspected consumer API/limitations.
- [x] Run workflow tests, validation tests, schema catalogue and whitespace checks;
  run one independent diff-first review and fix findings. The real CPU workflow
  also exercises assessment, status, resume, dry run and repeated package delivery.

Integration: commit/push the feature branch and open a linked PR closing #114.

## Review focus and risks

Fail closed on forged paths/duplicate identities, changing inputs during validation,
stale assessments, a package destination holding other bytes and a resume with
changed configuration. Tests must cover each. Hashes bind retained bytes, not signed
authenticity. Recovery recomputes partial work rather than inventing checkpoints.
Consumer acceptance and #79 eligibility are separate gates; a local dry run
establishes neither physical accuracy nor permission to switch training sources.
