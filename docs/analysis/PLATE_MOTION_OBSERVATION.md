# Motion observation and completed re-detection review

Date: 2026-10-18. Research only, development data only. Follows the owner instruction to
fetch main and proceed with the recommendations from the Claude-chat review. No selection,
tracker, calibration, canonical output or production default changes.

## Result

Vertical travel is useful as a warning, but does not establish target identity. The saved raw
EdgeTAM track on `self-clean-side-0203-17` has enough motion to clear the proposed threshold
despite its documented wrong-object segment. Guarded, correctly seeded tracks can produce the
opposite warning because the guard stops them early. Do not automatically try successive plates
and accept the first moving one on this evidence.

## Observation-only tool

`research/plate-detection/motion_observation.py` reads completed track JSONL and writes a separate
report. It opens no media, labels or models and changes no source track. Its development-input
gate uses the manifests and `clips.development_role` before reading any track, including an
unconditional refusal of `split-reserved-57`. Missing tracks, partial/unexpected files, invalid
coordinates, radii, scores, timestamps and track-identity mismatches fail closed. Output cannot
be inside the source run or overwrite an existing file.

The descriptive rule `motion-observation-v1` is:

- Use the first valid tracked radius as a fixed pixel normalization, explicitly **not** a manual
  physical reference or calibrated scale. Later radius changes do not change the normalization.
- Split at every lost sample and every gap longer than 0.5 s. Do not measure displacement across
  disconnected segments. Re-detection runs are refused in v1: their explicit restart/identity
  boundaries need separate handling before motion can be interpreted.
- Within each segment, weight each position by the time to the next valid sample, up to 0.5 s.
  These are left-endpoint weights for a statistic, not interpolated measurements. The final
  position has no following duration and therefore no percentile weight.
- Report the 98th minus 2nd weighted percentile of vertical position, and the untrimmed vertical
  range, in radii. Use the largest segment percentile span for the observation.
- Report `motion_present` at or above 1.5 radii, `insufficient_motion_evidence` below it, or
  `no_supported_intervals` without a valid sample pair. None is an identity verdict.
- Include valid record count, supported duration, supported fraction of the observed timeline,
  segment durations, the original seed decision and hashes of inputs and implementation.
  Supported duration means adjacent valid observations, not proven correct-object duration.

The threshold came from the already inspected development data in the Claude chat. This is not
an independently validated cutoff. Brief lifts can disappear from percentile spans in long
recordings; partial lifts and fragmented tracks can warn legitimately. Camera motion, other
lifters and tracking drift can produce motion. A high supported fraction cannot prove identity.

## Saved-track observations

Four completed runs, 27 clips each, were processed without changing their records. Counts below
include the two unlabelled exploration clips and tracks whose original seed decision refused;
the report never overrides those refusals. These are observation counts, not accuracy scores.

| Source run | Motion present | Insufficient motion evidence |
| --- | ---: | ---: |
| `run-2` (SAM 2.1, raw) | 26 | 1 |
| `run-2-guard-v2` | 26 | 1 |
| `run-edgetam-1` (raw) | 26 | 1 |
| `run-edgetam-1-guard-v2` | 24 | 3 |

Selected results (travel is the largest segment's weighted percentile span):

| Clip / source | Travel, radii | Supported time | Interpretation |
| --- | ---: | ---: | --- |
| `0215-27`, raw SAM 2.1 | 0.381 | 100% | Warns on the known wrong seed |
| `0203-17`, raw EdgeTAM | 2.360 | 65.3% | Motion present despite a known wrong-object segment |
| `0203-17`, guarded EdgeTAM | 0.061 | 21.1% | Correct initial track stops before useful lift motion |
| `1015-04`, guarded EdgeTAM | 1.470 | 13.9% | Correct initial track is truncated; warns near the cutoff |
| `0206-19`, guarded SAM 2.1 | 1.791 | 47.5% | Fragmentation leaves limited margin above the cutoff |

Raw SAM 2.1's smallest span among motion-present clips is 2.682 radii. This differs from the
chat's 2.8 minimum because the statistic here uses time weighting and explicit segmentation.
No threshold was adjusted after these reports were read.

Reports are local artifacts under `target/motion-observation-v1/`. The source-run hashes match
the four corresponding entries in [the existing SAM 3 report](PLATE_DETECTION_SAM3_SEED_RESULTS.md).

## Completed re-detection run

The other agent's `run-edgetam-redetect-1` initially had no score report. On checking its
completion, all 27 final JSONL files existed, with no partial files and no remaining per-clip
frame directories. The files were scored read-only using the main-branch `score_phase_a`
semantics and existing human labels, after checking every clip's current development role.
Track hashes were unchanged before and after scoring. No inference was rerun.

Same 25 scored development clips, 388 labelled frames:

| EdgeTAM run | Correct labelled | Wrong labelled | Lost labelled |
| --- | ---: | ---: | ---: |
| Original raw | 293 | 85 | 10 |
| Original guard-v2 | 283 | 66 | 39 |
| Re-detection | 314 | 68 | 6 |

The re-detection run has 23/25 clips correct at every labelled sample and 131/11,719 total
lost samples. It records six re-detection attempts, with four successful tracker restarts:

| Clip | Restart time(s), s | Labelled samples following each restart, until the next restart |
| --- | --- | --- |
| `0203-17` | 6.700 | 23 correct, 0 wrong, 0 lost |
| `1015-04` | 1.824; 5.690 | 4 correct each, 0 wrong, 0 lost |
| `0215-27` | 56.487 | 0 correct, 2 wrong, 2 lost |

None of the exact restart frames has a matching human label. These counts audit subsequent
labelled output, not the identity at the instant of restart or between labels. `0203-17` is
29/29 correct overall; `1015-04` is 10/10. `0215-27` remains 0/72 correct, with 68 wrong and
4 lost. `0206-19` accounts for the other two lost labelled samples (27/29 correct).

At the `0215-27` restart the recorded margin is **null**, with `refused: false`: there is no
eligible nonconcentric rival against which to measure a margin. Passing this check is not
positive identity evidence. This is a concrete limitation of relying on size plus score margin.

**Comparison limitation:** original EdgeTAM used torch `2.8.0+cu126`; re-detection used
`2.14.1+cu126`. The table compares saved runs, not a controlled attribution of improvements to
re-detection. A same-environment baseline is needed for that claim. Desktop per-frame latency
medians are about 0.08 s in both runs; these are not phone or end-to-end timings.

Private source: the existing Claude worktree's
`validation/private/plate-detection-sam3-seed/run-edgetam-redetect-1`.
Its `run.json` SHA-256 is
`a6f4a2c3fc229ca00632e5ec6c541afe6acb11e2edfddac1e2559abff2022500`.
Local `target/motion-observation-v1/redetect-score.json` and `redetect-audit.json` retain the
score, per-restart counts, source track hashes and hashes of the truth points used.

## Next steps and stop conditions

1. Keep motion reporting observation-only. Preserve human confirmation and ambiguity refusal;
   neither sufficient travel nor a passing detector margin can identify the intended lifter.
2. Before another research candidate, freeze how restart identity and insufficient coverage are
   handled, then compare raw, guard-only and re-detection in the same pinned environment. Include
   wrong-object duration and sparse-label limitations, not just recovered coverage. The present
   review cannot measure wrong-object duration between the one-second labels.
3. Freeze acceptance limits for wrong selection, refusal and runtime before independent new
   sessions. Include partial lifts, long setup, camera movement, carried plates and simultaneous
   lifters. Do not tune on or open reserved clips.
4. Phone work still requires an available paired Xiaomi and the approved runtime harness. Measure
   the complete intended workflow, including detector and fallback costs; desktop results do not
   establish phone feasibility. `adb devices -l` returned no connected devices in this session.

Verification: all 91 plate-detection unit tests passed, including nine motion-tool tests;
`schema_check.py` passed (15 documents). Regenerating all four completed-run motion reports in
memory matched the saved UTF-8 bytes exactly. Rust gates were not run because Rust code and
canonical contracts were unchanged.

## Reproduce the motion report

```powershell
python research/plate-detection/motion_observation.py --run-dir <saved-run> --manifest <data-checkout>/validation/private/manifest.json --manifest <data-checkout>/validation/private/vbt/manifest.json --output target/motion-observation-v1/<new-report>.json
python -m unittest discover -v -s research/plate-detection/tests -p 'test_*.py'
```

The completed re-detection score can be reproduced with the existing `score_phase_a.py`, using
`--run-dir <saved-redetect-run> --repo-root <data-checkout> --output <new-score-path>` **only after**
checking completion and current development roles as above. The scorer itself does not enforce
the manifest gate; the motion tool does. That remains a mechanical enforcement gap for direct
scorer invocation. The saved restart audit uses the scorer's `labelled_frames`, `is_target` and
`identity-loose-v1`, grouping labels from each `guard.status == redetected` to the next restart.
