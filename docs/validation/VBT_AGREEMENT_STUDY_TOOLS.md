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
  --output-dir <root>/validation/private/vbt/study-79/lock
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

For each enrolled slot the inventory reuses the existing evidence and reads **no velocity or scale
value**:

- session completion through the existing `vbt_session.py status` of the data checkout;
- the confirmed clip in the session record (a page `skipped` clip is a `confirmation` failure);
- the frozen method in the session record and run record: `csrt-all-v1`, `opencv-csrt`,
  `vbt-sg-0.15s-v1` expanded options, the locked plate diameter, 1.30 m, the pinned CLI SHA-256,
  the baseline commit without tracked changes, and output hashes;
- the retained #111 assessment, schema-valid and bound to the run record and pinned CLI, with all
  four statuses carried unchanged;
- novelty against the frozen exclusion list (its digest must equal
  `2c8cbc0275b11b94a5e89494d0190ec9a121d7c43c38fdd0679a2f26176894ea`), duplicates between slots,
  the video's container `creation_time` after the freeze instant and the session date not before the
  freeze day;
- exactly three increasing attempted-rep times, and the seed timestamp before the first;
- a scale-reference row for the clip, and the WL CSV.

A slot is **analyzable** when it was processed and has no derived failure in the `novelty`,
`confirmation`, `processing`, `assessment` or `wl_export` stages. A non-prospective clip (development
duplicate, recorded before the freeze) is therefore never paired. Other failures, including every
owner-declared one, keep the slot analyzable but make it **non-conforming**, which rules out PASS. No
slot leaves the 18-slot denominator.

Outputs, in this order: one consumer pairs file per lift with analyzable slots
(`pairs-<exercise>.json`, slot order, paths relative to the pairs file, `offsetS` 0; a lift without
analyzable slots gets none), the private `inventory.json` binding every input by exact-byte SHA-256
(including the pairs files), and `lock-summary.json`. The lock summary is public-safe: it holds only
the inventory digest, counts, `lockable`, freeze references, the exclusion digest, and the tool and
data commits. The inventory is lockable only when collection is not `in_progress`. Commit the lock
summary's digest, counts and references in an aggregate collection-lock note **before** the first
consumer invocation.

`verify` rehashes every recorded file and the inventory against the lock summary.

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

It passes only when there are three paired reps, every value matches, and both human reviews
(pairing against the video; seed and scale reference) are `confirmed`. It verifies that the consumer
checkout is the clean pinned commit. The output is private; its `public` block holds only status,
reasons, value counts and the largest velocity discrepancy.

## 4. Aggregate report and criterion

```powershell
python research/vbt-workflow/agreement79_report.py --root <pinned checkout> `
  --inventory <lock>/inventory.json --lock-summary <lock>/lock-summary.json `
  --runs-dir <runs> --handcheck <root>/validation/private/vbt/study-79/handcheck.json `
  --consumer-app <pinned consumer>/app --output docs/analysis/VBT_AGREEMENT_STUDY_RESULTS.md
```

The tool reruns `verify`, checks each report against the inventory (labels equal the pairs file,
input hashes equal the inventory, frozen parser, rule, overlap and loads), and compares run 1 and
run 2 byte for byte. It then evaluates the preregistered criterion as a pure function:

| Condition | Requirement |
|---|---|
| C1 | All 18 slots conforming; six per lift across three sessions |
| C2 | Every slot: three attempted reps, three detected per source, three pairs, both completeness flags true, no source-only or excluded reps; 18 pairs per lift |
| C3 | Per lift, serialized pooled primary LoA finite and inside [-0.050000, 0.050000], inclusive |
| C4 | Both runs byte-identical (JSON and Markdown) for every lift, inputs unchanged, hand-check passed |

The verdict is `PENDING` while collection is `in_progress`, `PASS` only when C1–C4 all hold, and
`FAIL` otherwise. Secondary metrics and scale diagnostics are reported but never enter it. The
Markdown is aggregate-only, with no paths, file names, fixture ids, session ids, dates or per-rep
values. It covers slot accounting, counts, per-lift and per-video statistics with unavailable
reasons, stick-to-plate scale ratios kept distinct from the geometric OpenBar/WL ratio,
reproducibility hashes, hand-check status and provenance. Its verdict line is a mechanical
evaluation; the reviewed decision is recorded separately under #79. Even a PASS authorizes no source
switch, eligibility promotion, production tracker choice, #58 closure or #80/M1 work.

Tests: `research/vbt-workflow/tests/test_agreement79_inventory.py`, `test_agreement79_handcheck.py`
and `test_agreement79_report.py`, synthetic data only. The bridge integration test runs real node
against the pinned consumer when `OPENBAR_CONSUMER_APP` points at its `app/` folder, and skips
otherwise.
