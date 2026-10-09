# Local VBT recovery and research handoff (#114)

These commands orchestrate retained files. They do not change canonical Analysis,
implement another parser or rep segmenter, write a recommender database, choose a
production tracker (#57), or switch training sources. #79's predeclared decision
and a reviewed consumer policy remain required for source switching or eligible
live-trial writes. A completed, mechanically valid run does not establish accuracy.

## Arrival, completion and recovery

`ingest` waits for the complete inbox snapshot (file names, modification times,
sizes and streaming SHA-256 hashes) to match across two polls 2 seconds apart.
Zero-byte arrivals cannot pass. `--inbox-timeout-s` defaults to 900 seconds and is
bounded to `(0, 21600]`. Identical videos arriving under different names remain
one clip. Distinct clips are supported by batch ingest; `time_session.py`, which
times one arrival, rejects several candidate videos instead of selecting the first.
Probing and copy hashes still validate container readability and retained bytes.
Two stable observations cannot guarantee a paused transfer will never restart;
the subsequent probe, input and copy/run hash checks fail closed if it changes.

`run --watch` ignores pre-existing unchanged CSVs and rejects several new
candidates. Stability now requires matching bytes, modification time and size
across polls, including a reset after disappearance. The existing session CSV
parser checks completeness and confirmation before any run output is written.
The existing bounded `--watch-timeout-s` remains in effect.

From the repository root:

```powershell
python research/vbt-workflow/vbt_session.py status --session demo
python research/vbt-workflow/vbt_session.py run --session demo --csv validation/private/vbt/sessions/demo/session-input.csv --plate-diameter-m 0.45 --stick-length-m 1.30 --tracker-policy csrt-all-v1 --preset vbt-sg-0.15s-v1 --resume
```

Supply the original `--sessions-root`, `--manifest`, analysis options and
`--openbar-cli`/`--gpu-python` when they were supplied to the original run. If the
run was interrupted before saving `session-input.csv`, use the original downloaded
CSV. Recovery reruns the existing pipeline, including tracker work; it does not
trust individual partial outputs as checkpoints. Without a final session record,
`--resume` permits the existing replacement path after its normal input preflight.
With a final record, it verifies hashes and requires the same CSV, manifest and
configuration, then returns without rewriting any output or running trackers.
An invalid final record is an error; use `--force` deliberately to replace it.
`--force` and `--resume` are mutually exclusive.

`status` returns 0 only when the final `session-record.json`, original state/page
and CSV, confirmed clip set, videos, seeds, label/click CSVs, per-clip final run
records, output hashes, manifest-entry bindings, analysis identities/provenance,
scale report and `report.html` match. It prints the report path for local viewing.
Absence of a record, malformed records, changed/missing evidence and duplicate
source identities return 1. This is processing completion, separate from #111's
mechanical, experiment suitability and accuracy assessments. Hashes bind retained
bytes, not signed authenticity. Do not edit or run the session during delivery.

Machine-origin `machine-run-record.json` is never substituted for the final
human-confirmed record. Machine-only handoff fails with an explicit research-only
message. If both workflows exist, handoff selects only the human-confirmed run;
machine analyses remain outside its candidate directory and cannot become trials.

## Dry run and outgoing package

Create retained #111 assessments with `assess_clip.py` for every selected lift,
using its final per-clip run record and the authoritative Rust validator. See
[VBT_CLIP_ASSESSMENT.md](VBT_CLIP_ASSESSMENT.md). Store them as
`<fixture-id>.assessment-v1.json` in one directory, then:

```powershell
python research/vbt-workflow/vbt_session.py handoff --session demo --tracker-policy csrt-all-v1 --assessments-dir validation/private/vbt/assessments/demo --output-dir validation/private/vbt/outgoing/demo --dry-run
python research/vbt-workflow/vbt_session.py handoff --session demo --tracker-policy csrt-all-v1 --assessments-dir validation/private/vbt/assessments/demo --output-dir validation/private/vbt/outgoing/demo
```

`--tracker-policy` is required for each handoff, using the existing named research
policies. It must select the retained run's tracker for each confirmed lift.
There must be exactly one candidate analysis per lift in the human session's
analysis directory; extra tracker or legacy analyses are an ambiguity error,
even with a named policy. Remove the ambiguity deliberately or rerun the session
through its existing stale-output cleanup. No analysis from a skipped clip is selected.

Assessments must pass their existing schema and bind the exact analysis, prediction,
optional geometry, run record, video and seed. Processing must be complete and
run/source/canonical checks valid. Other statuses are carried unchanged, including
unknown suitability, unknown PTS and accuracy `not_established`; none becomes
training eligibility. Retained validator/source hashes must still match. A stale
assessment fails rather than being silently regenerated or promoted.

Dry run constructs and validates the outgoing bytes without creating directories.
Delivery rechecks hashes after a 2-second poll and before publication; unstable
evidence is rejected for explicit retry rather than waited on indefinitely.
The destination must be separate from the session, under `validation/private/vbt/`
or `target/`. Publication stages a directory and renames it only after validation.
It contains unchanged selected `*.analysis-v1.json`, unchanged assessments,
`report.html`, `session-record.json` and a deterministic human-readable
`HANDOFF.txt` naming policy, statuses, consumer limitation and file hashes.
The receipt always says `research-only; consumer_eligible=false`.
Repeated dry runs have the same result; repeated delivery verifies the identical
existing package without rewriting it. A different or incomplete destination is
an error and is never overwritten. Consumer selection is limited to the analysis
files, one per source video, rather than every JSON file in the package.

## Inspected consumer API and compatibility gate

Inspected recommender main commit:
[`21ae95e4cb8c9d1fed53216b2bc9bbe083b3d572`](https://github.com/Szczepanov/adaptive-training-recommender/commit/21ae95e4cb8c9d1fed53216b2bc9bbe083b3d572).
The delivered [#981 importer PR #988](https://github.com/Szczepanov/adaptive-training-recommender/pull/988)
already supplies the consumer adapter. No OpenBar-side parsing/segmentation adapter is needed.

- [`parseOpenBarAnalysis(rawText, segmentationRule)`](https://github.com/Szczepanov/adaptive-training-recommender/blob/21ae95e4cb8c9d1fed53216b2bc9bbe083b3d572/app/src/observations/openBarAnalysis.ts)
  consumes a canonical `analysis-v1` JSON string, requires upward-positive metric
  coordinates, retains source/tracker/filter/kinematics provenance and uses the shared
  segmenter. The consumer owns its finite-value, timestamp and gap acceptance rules.
- [`proposeOpenBarTrial`, `openBarProposalToDraftRow`, `assignOpenBarOrdinals`](https://github.com/Szczepanov/adaptive-training-recommender/blob/21ae95e4cb8c9d1fed53216b2bc9bbe083b3d572/app/src/observations/openBarAnalysisImport.ts)
  preserve source refs and reject repeated video hashes. Analysis-file SHA-256
  identifies `openbar-analysis:sha256:<hash>`; source-video SHA prevents importing
  two tracker outputs as different trials.
- [`OpenBarImportPanel.handleFiles`](https://github.com/Szczepanov/adaptive-training-recommender/blob/21ae95e4cb8c9d1fed53216b2bc9bbe083b3d572/app/src/components/testing/OpenBarImportPanel.tsx)
  reads files in-browser, hashes/parses/proposes them using
  `CONCENTRIC_SEGMENTATION_V2` (delivered #984 shared rule), and deduplicates against
  current selection and the attempt. Preview is nonwriting. Apply populates draft
  rows; an explicit later Save persists them. There is no delivered importer CLI.

**Observed incompatibility:** the inspected `parseOpenBarAnalysis` rejects any
nonempty `provenance.tracker.implementation.parameters`. OpenBar's canonical
external-observation analyses retain tracker config and `prediction_sha256` there.
The existing consumer preview therefore rejects these workflow outputs. Do not
strip parameters or mutate analysis bytes to bypass it. Consumer-side acceptance
of retained provenance requires a reviewed consumer change; then reuse its existing
parser/segmenter and browser preview. Local outgoing-package dry run is the
available pre-#79 validation fallback and does not claim consumer acceptance.

The #79 readiness follow-up rechecked consumer main
`ea5e020792551e6c4f5f748cdee92fe50e5d677c` and confirmed the same blocker.
Its separate [consumer compatibility PR #1032](https://github.com/Szczepanov/adaptive-training-recommender/pull/1032) extends the existing parser for
parameterized tracker provenance and separates per-clip prediction hashes/time
bounds from method identity, including the persisted import identity checks.
The [prospective agreement preregistration](../plans/VBT_AGREEMENT_PREREGISTRATION.md)
records the tested consumer pin and execution gates. Main/browser acceptance
depends on integrating that consumer change; the OpenBar outgoing package and
assessment statuses remain research-only throughout.

Tests: `test_session_handoff.py`, `test_session_watch.py`, `test_time_session.py`
and the existing workflow suite. Canonical measurement, schema versions and golden
bytes are unchanged; no new dependencies, external implementation or media are copied.
