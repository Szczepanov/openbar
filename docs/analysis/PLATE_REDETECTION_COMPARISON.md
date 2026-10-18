# Same-environment EdgeTAM comparison

Date: 2026-10-18. Development research only. Execution record for the owner instruction after
merging #243: analyze and proceed with its recommendations. This reruns an existing evaluation
under standing agent authority; it records no new owner scope or production decision.

## Frozen execution scope (before the baseline run)

Question: with the package environment used by the completed `redetect-v1` run, how do the
unchanged raw tracker and `track-guard-v2` compare at the existing labelled timestamps?

- Reference: `run-edgetam-redetect-1`, run record SHA-256
  `a6f4a2c3fc229ca00632e5ec6c541afe6acb11e2edfddac1e2559abff2022500`, already scored in
  [the motion report](PLATE_MOTION_OBSERVATION.md). Its results are known, so this is descriptive
  development evidence, not a blind test or an independent confirmation.
- Baseline: main at `25a18fb`, existing `run_phase_a2.py --seed sam3 --tracker edgetam --stride 2`.
  All 27 reference clip IDs are explicitly supplied; every current manifest role must match the
  reference, and every media hash/probe must pass the existing harness checks. No reserved or
  new clips, new labels, candidate-selection changes, thresholds, models or dependencies.
- The existing plate-detection venv reports Python 3.11.9, torch 2.14.1+cu126, torchvision
  0.29.1+cu126, transformers 5.10.4, timm 1.0.20, numpy 2.2.6, OpenCV distribution 4.12.0.88,
  pillow 12.3.0 and huggingface_hub 1.33.0, matching the reference. CUDA 12.6 and the
  NVIDIA GeForce RTX 3060 Ti also match. Run offline against the existing checkpoint cache;
  compare the recorded checkpoint revisions and weight hashes before interpreting results.
- `clips.py`, `detectors.py`, `geometry.py`, `selection.py` and `truth.py` match the reference
  source hashes. The reference `track.py` additionally defines the re-detection chooser and
  optional method suffix; its underlying video tracker, initial seed chooser and circle output
  are unchanged. The baseline does not import the other worktree's unmerged implementation.
- Write the baseline only to this worktree's ignored
  `validation/private/redetection-comparison/raw-torch214-1`. Preserve the existing reference.
  Derive a separate guarded run with the existing `apply_guard.py`; never replace the raw run.
- Budget: one baseline configuration, 27 clips and the expected 11,719 sampled frames, up to
  45 minutes. No parameter search, additional inference configurations or repeated runs to
  improve a result. Stop on package/checkpoint/config/input-role/hash/timeline mismatch,
  non-finite data, incomplete output, resource-budget exhaustion or missing dependencies.
  Report the failure; do not install or download a fix.
- Score raw, guard-only and reference using the same unchanged `identity-loose-v1` scorer and
  human-label inputs. Before scoring, check all reference and baseline manifest roles and
  completeness again. Compare exact sampled frame indices/timestamps and seed decisions.
- Report correct, mismatching and lost labelled samples; whole-clip labelled success; total
  lost samples; identity changes at labelled samples; valid coverage; and desktop timing.
  Group reference labels by restart epoch. Label mismatches are not proof of wrong-object
  duration between labels; do not manufacture that duration by interpolation.
- No motion threshold is used to choose a candidate. No production pass/fail limit is assigned;
  independent-session acceptance limits and scope decisions remain the owner's.

The available ADB instance reports no connected devices. Phone measurements cannot run here.

## Scoring safeguard

The scoring CLI's known development-role enforcement gap is closed in this follow-up. Before
opening any candidates or labels it checks every recorded clip against current manifests using
the inference harness's existing `clips.development_role` policy. Missing fixtures, mismatched
roles, unseen frozen clips, exploration/validation conflicts and reserved clips refuse the run.
It defaults to the same two manifests as inference; repeated `--manifest` arguments override them.
This changes only CLI admission, not `score_run`, scoring tolerances or measurement semantics.
Synthetic tests demonstrate refusal before scoring and acceptance of eligible development roles.
PR review also repaired a pre-existing output-overwrite hazard at this entry point: the score is
serialized before exclusive output creation, so an existing run record, manifest, candidate,
label or score cannot be truncated by `--output`. Use a new score filename for a repeat. JSON
formatting and metric semantics remain unchanged. A preservation regression failed for all five
existing-file cases before the fix. All 94 research tests pass with warnings treated as errors.

The scorer admission change was made while the baseline inference ran. The runner does not import
the scorer; its inference source/configuration is unchanged. Its recorded inference-source hashes
therefore retain the original scorer snapshot, while the paired audit records its analysis code.

The main-branch phone harness (`validation/tools/phone_runtime_benchmark.py`, ADR-0009) measures
the native Rust CLI, not this Python EdgeTAM pipeline. Pairing a phone alone would not establish
an EdgeTAM runtime or authorize replacing that M0 boundary. No phone adapter or export dependency
is introduced by this comparison.

## Reference raw-evidence limitation

Review of the reference implementation found that its runner first assigns the old tracker
outcome to `tracker_candidates`, then replaces that field with the new session's result on a
successful restart (`run_phase_a2.py`, reference source hash
`41afcf34864e4dcc8aedf3a3860efcc9f01e8b59bba83e6002570e24137cde81`, line 175).
Thus, at four successful restart frames the reference retains only the post-restart result.
Pre-restart outcomes are unavailable, preventing reconstruction of those frames' guard inputs
and raw-output equivalence. Current-session naming is defensible, but this run cannot support
an unrestricted claim that all raw observations were preserved. Independent review confirmed
the gap. The reference files are not modified or relabelled.

The paired audit scores the saved output as emitted and excludes exact restart instants from
the raw-prefix comparison. A future re-detection implementation must preserve both outcomes
with explicit session provenance and a regression test before another run; the missing
historical observations cannot be recovered by changing the report. That unmerged implementation
is not imported or repaired here.

## Results

Completed one baseline run, approximately 23 minutes, inside the frozen budget. All 27 clips
and 11,719 frames are present. All three arms have identical frame indices, timestamps, PTS and
dimensions. All 388 existing labels match a sampled frame in every arm; their normalized truth
hashes match the previous #243 audit. No label was silently dropped or newly created.

The 27 initial seed outputs, including proposal rows and refusal decisions, match the reference
exactly. Across 11,078 frames strictly before each clip's first successful restart (or the entire
clip when there was no restart), the raw candidate rows match exactly. The derived guard-only
candidates also match the reference on those frames. There is no observed shared-path divergence
before restart. The remaining 641 frames start at the three affected clips' first restart and
cannot be treated as an unchanged tracker session.

### Paired labelled outcomes

`match` means geometric agreement under the existing `identity-loose-v1` centre/radius rule,
not independently proved object identity. A mismatch can also be localization error. Counts
include originally refused seeds, as descriptive research output, without overriding refusal.

| Arm, recorded torch 2.14.1+cu126 | Match | Mismatch | Lost | All labelled samples match |
| --- | ---: | ---: | ---: | ---: |
| Raw baseline | 293 | 85 | 10 | 23/25 clips |
| Baseline + guard-v2 | 283 | 66 | 39 | 21/25 clips |
| Saved redetect-v1 | 314 | 68 | 6 | 23/25 clips |

The old torch 2.8 baseline happens to have the same labelled totals, but this new comparison
establishes matching recorded environment, seeds, timelines and pre-restart outputs directly.
It is still a single descriptive development comparison, not repeatability or new-session evidence.
Nine scored clips have only one labelled frame; the two unlabelled clips have no identity score.

Guard-only to re-detection transitions across all 388 labels:

| Transition | Samples |
| --- | ---: |
| Match remains match | 283 |
| Mismatch remains mismatch | 66 |
| Lost becomes match | 31 |
| Lost becomes mismatch | 2 |
| Lost remains lost | 6 |

All 31 recovered matches are on `0203-17` (23) and `1015-04` (8). Both newly emitted mismatches
are on the already wrongly seeded `0215-27`; it remains 0/72 matching labelled samples. The
other residual losses are two samples on `0206-19`. No already-present guard match is lost.
Against raw tracking, re-detection adds 23 matching samples but loses two other raw matches,
for a net gain of 21. The two lost raw matches are the guard rejections on `0206-19`.

### All six attempts

Times are rounded here only; unrounded values and candidate indices are retained privately.
The latency column is the inclusive per-frame processing time, not isolated detector time.

| Clip | Time, s | Recorded decision | Margin | Frame latency, s |
| --- | ---: | --- | ---: | ---: |
| `0203-17` | 6.700 | Restart, no eligible rival | null | 0.7726 |
| `0215-27` | 55.425 | Refuse close scores | 0.03906 | 0.8363 |
| `0215-27` | 55.956 | No eligible candidate, no restart | null | 0.7879 |
| `0215-27` | 56.487 | Restart, no eligible rival | null | 0.7927 |
| `1015-04` | 1.824 | Restart, no eligible rival | null | 1.0135 |
| `1015-04` | 5.690 | Restart, clear score margin | 0.19922 | 0.9489 |

The first labels after the four restarts occur 0.300, 1.261, 0.183 and 0.317 s later respectively
in the table's successful-restart order. None of the exact restart frames is labelled. Subsequent
epoch counts remain those in [the earlier audit](PLATE_MOTION_OBSERVATION.md#completed-re-detection-run):
23 matches on `0203-17`, 4 matches in each of the two `1015-04` epochs, and 2 mismatches plus
2 losses after the `0215-27` restart. A null margin can accompany either useful or incorrect
output; absence of a rival is not positive identity evidence.

### Candidate availability and timing

These availability counts include all 27 clips, including the two unlabelled clips. A present
candidate can be the wrong object or outside the frame; availability does not establish validity.
Time coverage sums adjacent candidate-present intervals no longer than 0.5 s, over the observed
521.389 s total. It does not interpolate missing positions or measure correct-object duration.

| Arm | Candidate-present samples / 11,719 | Candidate-present interval coverage |
| --- | ---: | ---: |
| Raw | 11,565 (98.69%) | 98.05% |
| Guard-only | 10,979 (93.69%) | 92.02% |
| Re-detection | 11,588 (98.88%) | 98.01% |

Raw baseline median frame latency is 0.0795 s (nearest-rank p95 0.0816 s); the saved re-detection
median is 0.0800 s (p95 0.0871 s). The six attempt frames take 0.7726–1.0135 s each, which a
median conceals. Sums of recorded frame latencies are 924.1224 s and 941.0670 s respectively;
this difference is not an isolated measure of re-detection overhead. Guarded copies inherit
raw latency fields and omit replay cost. These timings exclude full decode/setup costs, are not
controlled performance benchmarks, and say nothing about phone feasibility.

## Recommendation after the comparison

Keep the current human-confirmed target boundary and motion reporting as observations.
Re-detection recovers useful coverage on two clips here but fails to resolve the wrong seed;
the score margin and radius window do not establish identity. Do not add automatic fallback to
the first candidate with sufficient motion or promote this candidate from these development data.

Before another re-detection run, repair raw preservation in its own implementation and test that
both pre- and post-restart outcomes survive (**done:** `run_phase_a2.py --redetect` now preserves
the pre-restart tracker output in `tracker_candidates` with `tracker_session_index` and records the
restarted session's output separately in `post_restart_tracker_candidates` with
`post_restart_session_index`, covered by
`test_run_clip_redetect_preserves_both_pre_and_post_restart_outcomes` in
`research/plate-detection/tests/test_phase_a2.py`). Then define how identity ambiguity and
insufficient coverage cause refusal, with owner acceptance limits, before an independent-session
freeze.

### Post-track and post-redetect refusal diagnostics (`post-track-diagnostics-v1`, #250)

`research/plate-detection/post_track_refusal.py` (`post-track-diagnostics-v1`, unit-tested in
`research/plate-detection/tests/test_post_track_refusal.py`) partitions a completed track at
`redetected` boundaries into disjoint identity epochs so motion (`motion_observation.observe`) and
supported time coverage are computed strictly within each epoch without bridging across a restart.
Without reading human truth labels, it evaluates four descriptive flags:

1. `seed_refused` — frame-0 seed was refused (`sam3-margin-v2`) or untracked;
2. `insufficient_coverage` — sum of within-epoch supported time intervals divided by clip duration
   is `< 0.50`;
3. `ambiguous_restart` — any restart epoch exceeds the physical jump-speed bound (`jump_radii / gap_s > 18.0 radii/s`)
   or has `observation != "motion_present"` after an uncontested (`margin = null`) or large (`> 1.5 radii`)
   spatial jump;
4. `no_motion_evidence` — no epoch in the clip reaches `motion_present` (`>= 1.5 radii` p02–p98 travel).

Descriptive results across the 27-clip development runs:

| Run / method | `seed_refused` | `insufficient_coverage` (`< 0.50`) | `ambiguous_restart` | `no_motion_evidence` | Total flagged clips | Unflagged clips on target |
| --- | --- | --- | --- | --- | ---: | ---: |
| `run-2-guard-v2` (`track_sam2_sam3seed_guard`) | `001`, `0219-28`, `vbt-45854c` (3) | `0215-27` (0.268), `0206-19` (0.475) | none (0) | `0215-27` (1) | 5 / 27 | 22 / 22 (20 / 20 labelled) |
| `run-edgetam-1-guard-v2` (`track_edgetam_sam3seed_guard`) | `001`, `0219-28`, `vbt-45854c` (3) | `0203-17` (0.211), `1015-04` (0.139) | none (0) | `0203-17`, `0215-27`, `1015-04` (3) | 6 / 27 | 21 / 21 (19 / 19 labelled) |
| `run-edgetam-redetect-1` (`track_edgetam_sam3seed_redetect`) | `001`, `0219-28`, `vbt-45854c` (3) | none (0) | `0215-27` epoch 1 (`margin=null`, travel `0.030 radii`) | `0215-27` (1) | 4 / 27 | 23 / 23 (21 / 21 labelled) |
| `run-edgetam-student-redetect-1` (`track_edgetam_dfinestudentseed_redetect`, 6 `val` + 2 stress clips) | none (0) | `0215-27` (`0.3885`) | `0215-27` epochs 1–20 (`jump=2.41 radii`, stationary) | `0215-27` (1) | 1 / 8 | 7 / 7 (all 6 unseen `val` clips `125/127` + `0203-17` epoch-1 recovery `15/29`) |

On `run-edgetam-redetect-1`, the two true-recovery clips (`0203-17`: `coverage = 0.9751`, epoch-1 travel
`4.67 radii`; `1015-04`: `coverage = 0.8848`, epoch-1/2 jump speeds `8.36` and `1.48 radii/s`, travel
`7.06` and `6.29 radii`) pass without flags, while the wrong-plate clip (`0215-27`) is flagged by both
`ambiguous_restart` and `no_motion_evidence`. On `run-edgetam-student-redetect-1` (100% Apache-2.0
`D-FINE-S` student seed + `rim-subpixel-v1` + `EdgeTAM` + `redetect-v1`), all **6 unseen session-disjoint
`val` clips** track cleanly without guard stops or flags (`125 / 127` labelled frames on target, **98.4%**,
including `27 / 29` on `0206-19` where the SAM 3 seed stopped at `7 / 29`), `0203-17` re-acquires the
moving foreground plate in epoch 1 (`2.70 radii` travel, `15 / 29` labelled frames), and `0215-27` is
flagged by `insufficient_coverage`, `ambiguous_restart`, and `no_motion_evidence`. Because these thresholds
were evaluated on the same 27-clip development pool, they remain descriptive diagnostics until the owner
sets acceptance limits for the next independent-session freeze.

The existing next-round plan still needs blind pilot labels, owner-confirmed refusal
policies and selectable side-view recordings from at least two new sessions. Existing exposed clips
cannot fill that gap; reserved clips remain untouched.

Phone timing remains blocked by the missing connection and by the absence of an EdgeTAM runtime
in the main-branch phone harness. No new runtime boundary, model export, package installation,
production behavior or owner PROCEED/STOP decision is made here.

## Retained evidence and reproduction

All generated artifacts remain ignored under `validation/private/redetection-comparison/`:
`execution-plan.md` (immutable pre-run scope), `execution-freeze.json`, `reference-label-audit.json`,
`raw-torch214-1/`, `raw-torch214-1-guard/`, `scores/`, `paired-audit.json` and `paired_audit.py` (the exact
one-off analysis source). The audit checks roles before reading tracks/labels, completeness,
paired timelines, all labels, source stability, checkpoint/config equality and truth hashes.
It uses the existing scorer's match helpers and records hashes of its analysis dependencies.

| Record | SHA-256 |
| --- | --- |
| Raw `run.json` | `2f99819390a4b9cc32ea41ce776ec6844c9ea37f087f161d4c8d967fcb56987e` |
| Guarded `run.json` | `e3eb1b0027efeb05685478fc2fd1507687919e3503d7118053bc464fbc7cfc54` |
| Reference `run.json` | `a6f4a2c3fc229ca00632e5ec6c541afe6acb11e2edfddac1e2559abff2022500` |
| `paired-audit.json` | `890983e6317ae3d8e3c50b8fdfe6e56da0cdb28c4b779c172198268ebd7cb5b2` |

To repeat the baseline later, use the existing pinned offline venv and checkpoint cache, the
explicit clip list in the raw `run.json` (whose hash is bound by `execution-freeze.json`), and the frozen harness command
above with a **new** private output directory. The private plan snapshot and run metadata are
the authoritative pre-run inputs; this report adds results after execution. Re-run the existing
`apply_guard.py` into another new directory. The retained one-off audit is invoked from this
checkout's root and refuses an existing audit output. No inference repeat is needed to inspect
the evidence or verify the scoring safeguard.

## Verification

- 94 plate-detection tests pass with warnings treated as errors, including refusal-before-I/O
  checks for the scorer admission change and existing-file preservation checks.
- 357 validation tests pass (one skipped); schema checking passes for 15 committed documents.
- Rust formatting, Clippy with warnings denied, workspace build and workspace tests pass.
- The gated scoring CLI scores all three completed arms successfully and agrees with the paired
  audit on every arm's matching-label total and all 388 labels.
- The initial audit replay at `483f454` produced byte-identical output. Replay after the output
  safety fix differs only in the scorer implementation hash; all observations and other fields
  remain unchanged, and the sealed audit is preserved. The state classifier also passes synthetic
  match/mismatch/lost self-checks.
- Independent reviews approve both the admission change and the report against retained evidence.
  No media was opened by those reviewers and no independent inference repeat is claimed.

No public fixtures, schemas, version constants, canonical serialization, calibration semantics,
dependencies or licensing change. No reserved clip was opened and no private artifact is committed.
