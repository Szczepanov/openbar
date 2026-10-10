# #79 study-execution tools

Study `owner-vbt-agreement-79-v1`. These tools carry out the post-collection steps of the
[frozen preregistration](../plans/VBT_AGREEMENT_PREREGISTRATION.md) mechanically. They were
committed before any study recording existed, so no tooling choice can follow an outcome. They add
no protocol, threshold, configuration, parser, segmenter or pairing logic: frozen values live in
one place (`research/vbt-workflow/agreement79_study.py`), and the pinned consumer
(`a74cb9dca24864af9f348bf9853fb6fe4f475521`) remains the only parser, segmenter, pairer and
statistics engine. Changing a frozen value is a protocol amendment that needs a new study identity.

All three tools are standard-library Python, never overwrite an output, emit deterministic UTF-8 JSON
or Markdown with LF endings, and exit `0` when they write a result (including failed or pending
results), `1` on invalid or inconsistent input, and `3` on an infrastructure problem (nothing written;
retry with unchanged inputs and pinned tools).

## Two checkouts

Study evidence is produced and kept in a clean detached checkout of the measurement baseline
`742225dc21d0d947db0ddf472218cd4dc442dc24`, whose git-ignored `validation/private/vbt/` holds the
sessions, assessments, slot log and outputs. These tools run from a later commit of this repository
and read that checkout through `--root`. The inventory refuses a data checkout that is not the clean
baseline, and records both commits. Paths recorded in evidence must be canonical repository-relative
POSIX spellings under `--root`; absolute, escaping, `./` or backslash spellings are refused.

## 1. Inventory, pairs files and collection lock

```powershell
python research/vbt-workflow/agreement79_inventory.py inventory --root <pinned checkout> `
  --slots <root>/validation/private/vbt/study-79/slots.json `
  --output-dir <root>/validation/private/vbt/study-79/lock `
  --consumer-app <pinned consumer>/app
python research/vbt-workflow/agreement79_inventory.py verify --root <pinned checkout> `
  --inventory <root>/validation/private/vbt/study-79/lock/inventory.json
```

`slots.json` (`owner-vbt-agreement-79-slots`, version 1) is the machine-readable form of the private
collection log: collection status (`in_progress`, `complete` or `concluded`), the locked plate
diameter, the 1.30 m stick length, the WL Analysis version (`not_visible` allowed), the assessments
folder, and exactly 18 slots in frozen order. An `enrolled` slot names its session id and date,
original file name, fixture id (or `null` if never processed), WL CSV path (or `null`), the
video-verified attempted-rep start times, and any owner-declared failures (`stage` and `reason`).
A `not_recorded` slot carries only its failures. Unknown keys are rejected.

`--consumer-app` is required: its parent must be a tracked-clean checkout of the pinned consumer
`a74cb9dca24864af9f348bf9853fb6fe4f475521`, otherwise the run aborts with exit 3.

For each enrolled slot the inventory reuses the existing evidence and reads **no velocity or scale
value**:

- session completion through the existing `vbt_session.py status` of the data checkout. Exit 0 is
  complete, exit 1 is a `processing:session_status_failed` verdict for every slot of that session
  (its last message line is kept privately as the session's `status_detail`). A Python traceback or
  any other exit code is an infrastructure abort;
- the confirmed clip in the session record (a page `skipped` clip is a `confirmation` failure) and
  its exercise (`processing:exercise_mismatch` if it differs from the slot's lift);
- the frozen method in the session record and run record: `csrt-all-v1`, `opencv-csrt`,
  `vbt-sg-0.15s-v1` expanded options, the locked plate diameter, 1.30 m, the pinned CLI SHA-256,
  the baseline commit without tracked changes, and output hashes;
- the retained #111 assessment, schema-valid and bound to the run record and pinned CLI, with all
  four statuses carried unchanged;
- novelty against the frozen exclusion list (its digest must equal
  `2c8cbc0275b11b94a5e89494d0190ec9a121d7c43c38fdd0679a2f26176894ea`), duplicates between slots,
  the video's container `creation_time` after the freeze instant (a missing or unparseable
  `creation_time` is `novelty:creation_time_unavailable`, because prospective collection must be
  verifiable) and the session date not before the freeze day;
- exactly three increasing attempted-rep times, and the seed timestamp before the first;
- a scale-reference row for the clip whose `source_video_sha256`, `analysis_sha256` and
  `click_csv_sha256` equal the slot's bound video, analysis and click CSV
  (`reference:scale_reference_unbound` otherwise; ratio values are never read), and the WL CSV.

Across slots it adds three non-blocking `protocol` notes. `environment_mismatch` marks a slot whose
run environment differs from the environment of the first processed slot in frozen order, compared
as canonical JSON. `session_has_unenrolled_clip` marks every enrolled slot of a session whose record
holds a clip fixture id or a `skipped` name that no slot of that session claims. `slot_order_violation`
marks a slot whose `creation_time` is not strictly after the previous slot in the same session that
has one, in the frozen order SQ1, SQ2, SN1, SN2, CL1, CL2.

Finally, every slot that is still analyzable and has both an analysis and a WL CSV goes through the
pinned consumer's own parsers. `agreement79_consumer_reps.mjs preflight` decodes both files as strict
UTF-8 and calls `parseOpenBarAnalysis(text, CONCENTRIC_SEGMENTATION_V2)` and
`parseWlAnalysisCsv(text, WL_ANALYSIS_CSV_PARSER_V2)` exactly as the consumer CLI does, and prints
only whether each was accepted. A rejection is `processing:openbar_parser_rejected` or
`wl_export:wl_parser_rejected`, and the parser message is kept only in the slot's private
`parser_preflight` field. If node or the bridge cannot run, or the bridge prints malformed output,
the run aborts with exit 3.

A slot is **analyzable** when it was processed and has no derived failure in the `novelty`,
`confirmation`, `processing`, `assessment` or `wl_export` stages. A non-prospective clip (development
duplicate, recorded before the freeze, unverifiable recording time) is therefore never paired. Other
failures, including every owner-declared one, keep the slot analyzable but make it
**non-conforming**, which rules out PASS. No slot leaves the 18-slot denominator.

The inventory is **lockable** only when collection is not `in_progress`, and a lockable inventory
also needs a tracked-clean tool checkout (otherwise exit 1). A non-lockable draft records
`tool_tree_clean` as found.

Outputs, in this order: for a lockable inventory only, one consumer pairs file per lift with
analyzable slots (`pairs-<exercise>.json`, slot order, paths relative to the pairs file, `offsetS` 0;
a lift without analyzable slots gets none and records `reason: no_analyzable_slots`); the private
`inventory.json` binding every input by exact-byte SHA-256 (including the pairs files); and
`lock-summary.json`. A non-lockable draft writes no pairs files, and every lift records
`pairs_file: null, reason: collection_in_progress`, so no consumer can run on an unlocked
collection. The lock summary is public-safe: it holds only the inventory digest, counts, `lockable`,
freeze references, the exclusion digest, and the tool and data commits. Commit the lock summary's
digest, counts and references in an aggregate collection-lock note **before** the first consumer
invocation.

Exit codes: `0` written; `1` invalid input, including a missing, unreadable, malformed or
wrong-digest exclusion list, an unresolvable path, an existing output folder, or a dirty tool checkout
for a lockable inventory; `3` a tool or process unavailable (git, ffprobe, node, a crashed
`vbt_session.py status`, the bridge, or a consumer checkout other than the clean pinned commit).

`verify` rehashes every recorded file and the inventory against the lock summary. A malformed or
unreadable inventory exits 1.

## 2. Consumer reports (pinned consumer, unchanged)

From the pinned consumer's `app/`, for each lift with a pairs file, two runs to new basenames without
`--force`:

```powershell
npm run evidence:velocity-agreement -- --pairs <lock>/pairs-back_squat.json `
  --output <runs>/report-back_squat-run1 --segmentation concentric-segmentation-v2 --min-overlap 0.5
npm run evidence:velocity-agreement -- --pairs <lock>/pairs-back_squat.json `
  --output <runs>/report-back_squat-run2 --segmentation concentric-segmentation-v2 --min-overlap 0.5
```

Repeat for `snatch` and `clean`. Keep `<runs>` under the data checkout's
`validation/private/vbt/study-79/`.

## 3. S1-SQ-1 hand-check

```powershell
python research/vbt-workflow/agreement79_handcheck.py --root <pinned checkout> `
  --inventory <lock>/inventory.json --consumer-app <pinned consumer>/app `
  --report <runs>/report-back_squat-run1.json `
  --pairing-review confirmed|failed|not_done --seed-reference-review confirmed|failed|not_done `
  --output <root>/validation/private/vbt/study-79/handcheck.json
```

The slot is fixed to `S1-SQ-1`. Rep windows come only from the pinned consumer's own parser,
invoked through `agreement79_consumer_reps.mjs` (`node --experimental-strip-types`; it imports the
consumer modules and adds no logic). The tool:

1. recomputes every OpenBar `vy_mps` from consecutive **filtered** positions and authoritative
   timestamp deltas with the OpenBar backward-difference rule, including the null pattern (first
   sample, gaps above `max_gap_s`, confidence below `min_confidence`), within 1e-9 m/s;
2. applies the consumer arithmetic to each paired window: a plain left-to-right mean (not Python's
   compensated `sum`), the peak, ROM as the difference of displacements relative to the first
   kinematics sample, and ECMAScript `Math.round(x * k) / k`;
3. compares the rounded mean, peak and ROM **exactly** with the parser and the report.

Each parser rep must also cover exactly its window: start and end timestamps equal the window's,
and `frameCount` equals the window length (else `window_mismatch`).

It passes only when there are three paired reps, every value matches, and both human reviews
(pairing against the video; seed and scale reference) are `confirmed`.

`--report` is optional only when `S1-SQ-1` was not paired: the slot is not analyzable in the
inventory, or the back_squat lift has no pairs file. The tool then writes status `failed` with
`slot_not_analyzable` and `inputs.report: null`. If `S1-SQ-1` is analyzable and back_squat has a
pairs file, `--report` is required (exit 1 without it).

It verifies that the consumer checkout is the clean pinned commit (exit 3 otherwise), and records the
tool checkout's `tool_commit` and `tool_tree_clean`. A tool checkout with tracked changes is refused
(exit 1). The output is private and written once; its `public` block holds only status, reasons
(tool codes), value counts and the largest velocity discrepancy.

## 4. Aggregate report and criterion

```powershell
python research/vbt-workflow/agreement79_report.py --root <pinned checkout> `
  --inventory <lock>/inventory.json --lock-summary <lock>/lock-summary.json `
  --runs-dir <runs> --handcheck <root>/validation/private/vbt/study-79/handcheck.json `
  --consumer-app <pinned consumer>/app --output docs/analysis/VBT_AGREEMENT_STUDY_RESULTS.md
```

The tool reports only a **lockable** inventory: both the inventory and its lock summary must record
`lockable: true` (collection `complete` or `concluded`). Anything else exits 1 and writes nothing,
so statistics are never rendered for a collection in progress.

The tool reruns `verify`, checks each report against the inventory (labels equal the pairs file,
input hashes equal the inventory, frozen parser, rule, overlap and loads), and compares run 1 and
run 2 byte for byte. It reads the git state of its own (tool) checkout. It binds the hand-check: the
recorded inventory and back_squat run 1 report hashes must match, and `inputs.report` may be null
only when there is no back_squat report. `consumer_commit` must be the pinned consumer, the freeze
references must be the frozen ones (when recorded), and status `passed` must have no reasons. Any
breach turns the hand-check into `failed` with `handcheck_inputs_mismatch`. It then evaluates the
preregistered criterion as a pure function:

| Condition | Requirement |
|---|---|
| C1 | All 18 slots conforming; six per lift across three sessions |
| C2 | Every slot: three attempted reps, three detected per source, three pairs, both completeness flags true, no source-only or excluded reps; 18 pairs per lift |
| C3 | Per lift, serialized pooled primary LoA finite and inside [-0.050000, 0.050000], inclusive; every produced report has the same `openBarMethodConfigSha256` (else `method_config_mismatch_across_lifts`) |
| C4 | Both runs byte-identical (JSON and Markdown) for every lift, inputs unchanged, hand-check passed; the tool checkout tracked-clean (`tool_tree_dirty`) and at the lock summary's `tool_commit` (`tool_commit_differs_from_lock`); the hand-check's `tool_commit` equal to it (`handcheck_tool_commit_differs`) |

`PASS` requires C1–C4 to hold; anything else is `FAIL`. The pure function still returns `PENDING`
for a collection `in_progress`, but the command never reaches that case. Secondary metrics and scale
diagnostics are reported but never enter the verdict.

The Markdown is aggregate-only, with no paths, file names, fixture ids, session ids, dates or per-rep
values. It covers slot accounting, counts, per-lift and per-video statistics with unavailable
reasons, stick-to-plate scale ratios kept distinct from the geometric OpenBar/WL ratio,
reproducibility hashes, hand-check status and provenance. Slot accounting also counts, per slot, the
`lost` and `low_confidence` raw observations (`raw_observations[].tracking_state`) of the slot's
analysis. These are counts only, never values. They show `unavailable` when the analysis no longer
matches its inventory hash or cannot be read.

Owner free text is rendered only when it looks like a code. A failure shows `stage:reason` only when
each part matches `^[a-z0-9_:.-]+$`; otherwise it shows `stage:[owner note]`. A hand-check reason that
is not a code shows as `[unrecognized reason]`. The WL Analysis version appears only when it matches
`^[A-Za-z0-9 ._()+-]{1,40}$`, otherwise `[owner note]`. Every cell is also redacted against the
inventory's private strings and Markdown-escaped.

The verdict line is a mechanical evaluation; the reviewed decision is recorded separately under #79.
Even a PASS authorizes no source switch, eligibility promotion, production tracker choice, #58 closure
or #80/M1 work.

Exit codes: `0` written; `1` invalid or inconsistent input (including a non-lockable inventory or an
existing output); `3` infrastructure (git or node unavailable, or a consumer checkout other than the
clean pinned commit).

Tests: `research/vbt-workflow/tests/test_agreement79_inventory.py`, `test_agreement79_handcheck.py`,
`test_agreement79_report.py` and `test_agreement79_report_criterion.py` (the criterion truth
table), synthetic data only. The bridge integration test runs real node
against the pinned consumer when `OPENBAR_CONSUMER_APP` points at its `app/` folder, and skips
otherwise.
