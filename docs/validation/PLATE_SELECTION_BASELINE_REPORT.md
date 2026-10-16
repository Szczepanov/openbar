# Plate-selection baseline: frozen evaluation report (aggregates only)

Issue: #178. Date: 2026-10-16. Mode: **descriptive**; no pass, promotion or threshold verdict.

The baseline automatic plate proposer `plate-hough-edge-v1` (unchanged `PlateParams`) was run
once on 13 frozen clips and scored once against two independent blind label passes. The media,
frames, label coordinates and private records are not published; this file carries their hashes
and the non-private results only.

**Result in one line:** the baseline selected the labelled plate on none of the 10 scorable
selectable clips (0/10, 95% Wilson 0–28%). Five of its emissions were 190–760 px from the plate,
two clips were refused, and three emissions were near the plate but outside the frozen matching
tolerance. Under the protocol's own readiness rules the run is **insufficient evidence** for any
ranking-versus-proposal attribution (see [Readiness](#readiness-and-deviations)).

## Provenance

| Item | Value |
| --- | --- |
| Owner decision | PROCEED (research-only baseline evaluation), [#178 comment](https://github.com/Szczepanov/openbar/issues/178#issuecomment-6211851867), 2026-10-16. It extends the 2026-10-15 "Prompt 1 only" decision in the plan, which allowed preparation but no real-media scoring. |
| Plan / split doc commit | `ffab4e13e4182953e38394d588c50de8ecc3e578` (shared-pool amendment, #228) |
| Executor | `research/vbt-workflow/plate_selection_eval.py` at `ca451956f083f00ede8dd73fce89e8cce253b84f` (#229) |
| Scorer | `research/vbt-workflow/plate_selection_score.py` at `4e5bcd76d5b5ec429f859b9c9ba5befd14c4ae8b` (#230) |
| Freeze record | `plate-selection-freeze-20261016-1`, SHA-256 `6f21df221be69e36ee0a8972c7b505c741a286c97c9417af083b7dd7ed25b064` |
| Private manifest | SHA-256 `b3fb03cd8600497e5da59bbb579c375c3c8774130d98bc893fc19390ca59f0ef` |
| Candidate parameters | `PlateParams` defaults, SHA-256 `73c8a341cf909cd764373f22e64912cdfc632ca9e698175ec633d7616ebcd9ae` |
| Observations | `observations.json`, SHA-256 `a74c57c7cbe59fc62734087214fbaa0d2b31720c3d659ee975d86a3272be1e2a` |
| Score | `score.json`, SHA-256 `f75b743f1e69abe8fb6fe1627a7d2b06e09a3d26cfd5e9db6c90e9ce45b4f32c` |
| Environment | Windows-10-10.0.26300-SP0; Intel64 Family 6 Model 183 (i5-13600K class), 64 GB RAM, GPU unused; Python 3.11.9, OpenCV 4.12.0, NumPy 2.2.6, FFmpeg 9.0.2 |

Private files (git-ignored, primary checkout): `validation/private/frozen-eval/freeze/` and
`validation/private/frozen-eval/runs/plate-selection-freeze-20261016-1/`.

## Frozen protocol

- **Clips:** 13 `split-frozen-eval` clips with manifest `purpose: validation`, used under the
  shared-pool rule (PLATE_SELECTION_SPLIT.md). Six recording sessions: 2026-10-15 (8 clips) and
  2023-02-03, -06, -12, -13, -15 (one clip each). No `split-reserved-57` clip.
- **Decision frame:** one per clip, requested time 0.0 s (first decoded frame), horizon 0.0 s.
  Chosen without labels or candidate output.
- **Candidate:** unchanged `suggest_plate`; its own tie order and refusals only, no added rules.
- **Labels:** two blind passes (`a`, `b`, same owner, second pass a day later); blind
  adjudication of the 13 frames whose passes differed by more than 8 px.
- **Matching:** reference = mean of agreeing passes at the decision frame; match when centre
  error ≤ Tc = 5.4 px and radius error ≤ Tr = 5.5 px (frozen; derived from the passes' own gap
  at the decision frame).
- **Budget:** CPU only, no network, 60 s per clip, 5 timing repeats.

## Outcomes

Policy groups: S (selectable) 10, I (indeterminate) 3, R/U (required refusal) 0.

| Measure | Count | 95% Wilson |
| --- | ---: | ---: |
| Accepted-correct / S | 0 / 10 | 0–28% |
| Wrong target / S | 8 / 10 | 49–94% |
| Missed or refused / S | 2 / 10 | 6–51% |
| Wrong target / emitted S | 8 / 8 | 68–100% |
| Timing failures, timeouts, executor errors | 0, 0, 0 | — |
| Unsafe acceptance / required refusal | not estimable (no R/U clips) | — |

Emissions: 10 of 13 clips. The three indeterminate clips are reported, not scored:

- `self-clean-side-0203-17`: the plate is fully occluded at the decision frame in both passes
  (expected policy `indeterminate`, frozen before the run); the baseline emitted a circle.
- `self-snatch-side-0213-25`: the freeze record marks its adjudication unresolved (all three
  passes disagreed at another frame, 362), so the record's flag makes the reference unresolved;
  the baseline emitted a circle.
- `self-snatch-side-1015-03`: passes a and b differ by 5.44 px at the decision frame, just over
  Tc = 5.4 px; the baseline refused (support 0.32 < 0.4). See deviation 3.

## Per-clip results (S clips)

Errors are from the emitted circle to the reference, in display pixels; relative radius error is
`100 * (proposal - reference) / reference`.

| Clip | Session | Outcome | Centre error px | Radius error % | Support |
| --- | --- | --- | ---: | ---: | ---: |
| self-back-squat-side-1015-06 | 2026-10-15 | wrong target | 762.5 | −82 | 0.56 |
| self-back-squat-side-1015-09 | 2026-10-15 | refused (support 0.38 < 0.4) | — | — | — |
| self-back-squat-side-1015-10 | 2026-10-15 | wrong target | 11.0 | −2 | 0.51 |
| self-other-side-0215-27 | 2023-02-15 | wrong target | 643.0 | −51 | 0.58 |
| self-snatch-side-0206-19 | 2023-02-06 | wrong target | 11.5 | +2 | 0.47 |
| self-snatch-side-0212-24 | 2023-02-12 | wrong target | 3.5 | −18 | 0.77 |
| self-snatch-side-1015-01 | 2026-10-15 | refused (support 0.37 < 0.4) | — | — | — |
| self-snatch-side-1015-02 | 2026-10-15 | wrong target | 189.5 | −66 | 0.40 |
| self-snatch-side-1015-04 | 2026-10-15 | wrong target | 407.4 | −66 | 0.48 |
| self-snatch-side-1015-05 | 2026-10-15 | wrong target | 459.7 | −70 | 0.46 |

Observations, not tuned conclusions:

- **Five far misses** (1015-06, 0215-27, 1015-02, -04, -05) are 190–760 px from the plate with
  circles 51–82% smaller than it. No plausible tolerance makes these correct. What they hit is
  unknown: there is no scene inventory, so the wrong-target class is `unknown` for every case.
- **Three near misses.** 1015-10 and 0206-19 are about 11 px off in centre with radius within
  ±2%. 0212-24 is 3.5 px off in centre, but its radius is 18% too small (a possible inner rim).
  They fail the frozen 5.4 / 5.5 px tolerance. They are reported as wrong targets and were not
  re-scored.
- **Support does not separate right from wrong here:** the highest support (0.77) is a near
  miss and the far misses range 0.40–0.58.

By session, accepted-correct is 0/7 for 2026-10-15 and 0/1 for each of 2023-02-06, -12, -15.
2023-02-03 and -13 have no S clip. Clips within a session are correlated, so the pooled
intervals overstate population precision.

## Candidate stages (all 10 S clips)

Membership uses the same matching rule on the unrounded stage geometry.

| Stage | Matching S clips |
| --- | ---: |
| Raw Hough proposals | 0 / 10 |
| After the first-60 cut | 0 / 10 |
| After fit checks | 0 / 10 |
| Ranked top-1 / 3 / 5 / 10 / 20 | 0 / 10 each |

K=20 funnel: raw-generation loss 10 / 10; pre-refinement top-1 correct 0 / 10; refinement
changed no outcome. Pre-refinement support margins on the S clips: median 0.085, range
0.003–0.40, n = 10. Raw proposals per clip ranged 11–364.

## Latency (13 clips, desktop, diagnostic only)

| Stage | Median s | Max s |
| --- | ---: | ---: |
| Search, cold (first call per clip) | 0.054 | 0.49 |
| Search, warm (median of 5 repeats) | 0.053 | 0.50 |
| Whole clip incl. probe, decode and repeats | 4.5 | 8.2 |

The wall time is dominated by probing and decoding the full stream. This says nothing about the
Android or end-to-end budget (#179).

## Readiness and deviations

Stated before interpreting anything above:

1. **Matching tolerances did not come from a separate pilot.** HOUGH_TOP_K_PREPARATION.md
   requires Tc/Tr from a source-separated repeatability pilot, including an outer-rim radius
   repeat; without it, matching is "NOT READY" and the permitted conclusion is **insufficient
   evidence** for the stage attribution. Tc/Tr were taken from the evaluation clips' own A/B gap
   at the decision frame (n = 12). The raw-generation-loss count of 10/10 is therefore an
   observation under a strict, label-noise-sized tolerance, not a demonstrated proposal failure.
2. **No scene or rim inventory.** Identity is geometric only. Concentric or nearby distractors
   are not resolved and the wrong-target taxonomy is unknown.
3. **Tc was rounded when frozen.** The nearest-rank 95th percentile of the frame-0 centre gaps
   was 5.44 px; the record froze 5.4 px. Exactly one clip, 1015-03 (gap 5.44 px), became
   indeterminate because of the rounding. With 5.44 px it would have been a refused S clip,
   giving accepted-correct 0/11. The record was not changed after scoring.
4. **Same annotator twice.** Passes a and b are by one person a day apart: intra-annotator
   repeatability, not independent annotators.
5. **Radius was not adjudicated.** Only centres were adjudicated, and none at a decision frame.
6. **Onsets are upper bounds** from a ~1 s label grid. The decision frame (0.0 s) precedes every
   labelled onset, so no timing failure arose.
7. **No refusal cases.** All clips were `select` or `indeterminate`, so unsafe acceptance and
   correct refusal are not estimable.
8. **Agent previews.** Before the freeze, an agent viewed three decoded frames per clip of these
   sessions to assign exercise and view tags. Nothing was tuned on them.
9. **Coverage.** Back squat comes from one session; clean coverage is one clip (0203-17, here
   indeterminate); 0215-27 is a push press/jerk, not a required exercise; the 450 mm plate is
   assumed for the 2023 clips; more than one lifter appears in the 2023 footage; exercise and
   view labels for most 2023 clips are agent-assigned and unverified.
10. **Shared pool.** These clips are also the #57 held-out pool. Neither study may tune on them;
    any candidate revised after this unblinding makes the pool development-only for both
    studies.

## What this does and does not support

- It supports: on these 10 clips the unchanged baseline did not select the labelled plate at the
  first frame, and half of its emissions were far from any plate geometry.
- It does not support: a population accuracy, a ranking-versus-proposal attribution, a support
  threshold, an audit sampling rate, or any change to #95/#113 or the canonical pipeline.
- Any revised candidate needs a new frozen split and a new freeze record.
