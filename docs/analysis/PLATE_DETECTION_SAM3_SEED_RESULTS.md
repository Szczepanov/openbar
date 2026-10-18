# SAM 3 seed with SAM 2.1 or EdgeTAM tracking: development result (#233, #57)

Date: 2026-10-18. Research only, development clips only. Nothing here is a frozen or held-out result, and no
number below generalises beyond these clips. Decisions: the owner chose SAM 3 as the lead plate-selection
candidate and approved an EdgeTAM desktop test on 2026-10-17
([#233](https://github.com/Szczepanov/openbar/issues/233), [#57](https://github.com/Szczepanov/openbar/issues/57),
licence review in [SAM3_LICENCE_REVIEW.md](../legal/SAM3_LICENCE_REVIEW.md)). Builds on
[Phase A](PLATE_DETECTION_PHASE_A_RESULTS.md) and [Phase A.2](PLATE_DETECTION_PHASE_A2_RESULTS.md); code in
`research/plate-detection/`.

## Questions

1. Does a SAM 3 seed on the label-free frame 0, tracked with the unchanged SAM 2.1 video tracker, hold the right
   plate better than the A.2 GroundingDINO seed?
2. Can EdgeTAM, Meta's on-device SAM 2 variant (Apache-2.0), replace SAM 2.1 as the tracker without losing
   accuracy? The phone needs a tracker it can run; SAM 2.1 and SAM 3 cannot run there.

## Candidate `sam3-seed-v2` (frozen before its run)

- **Seed:** SAM 3 proposals on frame 0 ("weight plate", "barbell plate"), the existing plate-size window,
  `in-frame-v1` (centre inside the frame), and only rows with a mask the tracker can start from. The seed is the
  top-1 of these.
- **Refusal `sam3-margin-v2`:** refuse when a candidate not centred on the best (more than 25% of its radius away)
  scores less than 0.05 below it. Margins are rounded to the stored 5 decimals before comparing. `close-score-v1`
  is recorded beside it for comparison only.
- **Tracking:** the A.2 streaming loop, stride 2 plus every labelled frame, rim fit per mask. An empty mask is a
  lost frame with no coordinate.

The first version (`sam3-seed-v1`, commit 6e85c50) was reviewed before its run. The review found a
float-dependent margin boundary, a seed that could be accepted without a trackable mask, concentric rims counted
as rivals, and a mislabelled refusal in `score.json`. The first full run was stopped unread and deleted, and the
rules were re-frozen as v2 (4c2d531). What was known when the rules were fixed is listed in `selection.py`:
`in-frame-v1` comes straight from the A.2 failure on `self-other-side-0215-27`, and the 0.05 margin sits below the
one known score gap (0.95 against 0.87 on `self-back-squat-side-1015-06`). A one-clip smoke run of v1 showed one
margin. Treat the refusal numbers as optimistic.

EdgeTAM was added afterwards as a tracker option only (d4c8ae8, d0caa1b): same seed rule, same loop, a different
checkpoint (`yonigozlan/EdgeTAM-hf` revision c266ce53, a transformers conversion of `facebook/EdgeTAM`, 13.9M
parameters). Its RepViT backbone is built by `timm` 1.0.20 (Apache-2.0), added to the research requirements for
this owner-approved model. `timm` read only a backbone config file (`timm/repvit_m1.dist_in1k/config.json`) from
the Hub cache; all weights come from the hashed EdgeTAM checkpoint. That config file is not pinned or hashed.
Neither run's `run.json` records the `timm` or torchvision version or the GPU; later records do.

## Results

Same 27 development clips (25 scored), 388 labelled frames, 11,719 tracked frames, owner labels, `identity-loose-v1`.
Both runs used torch 2.8.0+cu126 (recorded) on the owner's desktop GPU, an RTX 3060 Ti (not recorded in these runs). A.2 is the earlier GroundingDINO seed with SAM 2.1 tracking.

| | Clips on target on every labelled frame | Labelled frames on target | Frame-0 refused (would be correct) | Accepted frame-0 correct | Lost frames | Centre err / r, median (p90) | Radius err, median (p90) | Median s/frame |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A.2: GroundingDINO seed + SAM 2.1 | 22/25 | 276/388 | 1 (0) | 18/19 | 36/11,719 | see A.2 | see A.2 | see A.2 |
| **SAM 3 seed + SAM 2.1** | **24/25** | **316/388** | 1 (1) | 18/19 | 0/11,719 | 0.022 (0.069) | 0.013 (0.045) | 0.33 |
| SAM 3 seed + EdgeTAM | 23/25 | 293/388 | 1 (1) | 18/19 | 153/11,719 | 0.022 (0.065) | 0.013 (0.040) | 0.08 |

Geometry is over labelled frames where the tracked plate is the labelled one, so the two trackers' geometry is
computed over different frame sets (316 and 293 frames) and only the medians are compared below. The median seconds per frame include
the SAM 3 seed on frame 0 and the rim fit; they are desktop GPU timings and say nothing about a phone.

### SAM 3 seed

- **Fixed:** the two A.2 seed errors that SAM 3 ranks correctly. `self-back-squat-side-002` goes from 0/25 to 25/25,
  and `self-back-squat-side-1015-06` from 0/15 (refused, and wrong) to 15/15.
- **Remaining miss: `self-other-side-0215-27`, 0/72.** `in-frame-v1` removed the plate centred off the left edge
  (x = −23 px), but the seed is still a nearer plate, not the labelled plate on the racked bar in the background.
  Its margin was 0.059, just above 0.05, so `sam3-margin-v2` accepted it. `close-score-v1` would have refused it,
  along with 6 other clips whose seed was right.
- **False refusal: `vbt-45854c8cdf352a6f`.** The margin was 0.047 between two plates; the seed was correct.
- **Refusals overall: 3 of 27 frame-0 decisions.** Besides `vbt-45854c8cdf352a6f`, the rule refused the two
  unscored exploration clips (`self-snatch-ohs-side-001`, `self-snatch-unknown-0219-28`, margin 0.020 each), which
  have no labels, so whether they were right is unknown.
- On the 24 clips with a correct seed, SAM 2.1 kept the plate on every labelled frame and lost no frame.

### EdgeTAM against SAM 2.1, same seeds

- **Same as SAM 2.1 on 24 of 25 clips,** with the same median centre and radius errors, at about a quarter of the
  desktop time per frame.
- **`self-clean-side-0203-17`: lost, then a silent wrong track.** EdgeTAM tracked the plate for 6.2 s, reported it
  lost for 8 s (no coordinate, correctly), and from 14.1 s locked onto a different object at plate-like radii
  (36–169 px at the labelled frames; 22–413 px between them). 13 labelled frames between 15 s and 28 s are on the
  wrong object. Its tracker score there was 0.56–0.98, against 0.97–1.0 while on target, so a score threshold might
  catch some of these frames, but no rule in this run flags them. SAM 2.1 tracked the whole clip correctly. This breaks "lost means no coordinate" in spirit: a wrong
  position is reported as a tracked one.

## Identity guard after a loss (`track-guard-v2`)

To stop a tracker from reporting a wrong object as the plate, `guard.py` post-processes a track into a separate
derived method (`apply_guard.py`; the raw track is kept). A frame whose centre moves more than 18 radii/s
(+0.25 radius) or whose radius changes by more than 15% against the last accepted frame is rejected, with no
coordinate. When no frame has been accepted for more than 0.5 s, the track stops until a new seed. The guard reads
only the tracked circles and timestamps, never labels.

Both versions were designed after seeing the EdgeTAM failure above. The first version (`track-guard-v1`) ended the
track at the first implausible step; on the SAM 2.1 run it stopped 7 correct tracks at single-frame mask glitches
during fast snatch pulls (fitted circles jumping 20-40 radii/s with 20-130% radius swings), so v2 rejects such
frames instead. v2 was then chosen after seeing v1's results. Nothing here is evidence of how either behaves on
new sessions.

Labelled frames, raw against guarded (`identity-loose-v1`):

| Track | On target | On a wrong object | Lost (no coordinate) |
| --- | --- | --- | --- |
| SAM 3 seed + SAM 2.1, raw | 316 | 72 | 0 |
| SAM 3 seed + SAM 2.1, guarded | 301 | 16 | 71 |
| SAM 3 seed + EdgeTAM, raw | 293 | 85 | 10 |
| SAM 3 seed + EdgeTAM, guarded | 283 | 66 | 39 |

- **What it fixes:** EdgeTAM's re-lock on `self-clean-side-0203-17` goes from 13 wrong labelled frames to 0; they are
  lost instead. The guard stops the track 0.5 s after the loss began.
- **What it does not fix:** a wrong seed. Most wrong frames left (16 and 66) are `self-other-side-0215-27`, where
  the seed itself is the wrong plate and the track is smooth.
- **What it costs:** a fast pull where the mask glitches for longer than 0.5 s ends a correct track. SAM 2.1 loses
  15 labelled frames on `self-snatch-side-0206-19`; EdgeTAM loses 2 there and 8 on `self-snatch-side-1015-04`
  (stopped at 1.8 s).
- **Suggests:** stopping is the safe default, but the track should be resumed by re-detecting the plate (SAM 3 on
  desktop, a distilled detector on the phone) and checking it against the seed, rather than staying lost. A better
  rim fit would also cut the glitches that trigger the step rule.

## Limits

- 25 scored clips from few sessions; one or two clips decide each difference. Development estimates only.
- The refusal rule was chosen knowing the facts listed above; its one catch and one false refusal are not evidence
  of how it behaves on new sessions.
- SAM 3 ran in bfloat16, so its scores come in steps of 1/256 (about 0.004). The 0.05 margin falls between 12 and 13
  steps: `vbt-45854c8cdf352a6f` (12 steps, refused), `vbt-bb101cea545505fc` (14 steps, accepted with a correct
  seed) and `self-other-side-0215-27` (15 steps, accepted with a wrong seed) are one to three steps from the other
  outcome, which a different GPU or library version could change.
- A candidate centred on the best one is never a rival, which protects the plate's identity but not its scale: if
  an inner rim or hub ever outscored the outer rim, it would become the seed unchallenged and its smaller radius
  would be tracked. No clip here shows that.
- Nine of the 25 scored clips (eight `vbt-*` clips and `self-hang-snatch-side-002`) have a single labelled frame,
  so for them "on target on every labelled frame" only checks the seed.
- Labelled frames are 1 s apart in the long clips, so a short wrong segment between labels can be missed. The
  EdgeTAM failure is long enough to show; shorter ones may not.
- GPU inference is not bit-for-bit reproducible across GPUs or library versions; `run.json` records packages,
  checkpoint revisions, weight hashes and source hashes.
- Both trackers predict masks at 256×256 before upscaling, so each mask cell spans several source pixels. The 1.3%
  median radius error is far from the ±0.5% scale a VBT number needs, for both trackers.
- No candidate enters canonical `Analysis` or a #95/#113 session record; all output is machine-origin.

## What this suggests (no decision taken)

1. A SAM 3 seed improved on the GroundingDINO seed on these clips. The two-plate scene is
   still a job for a human tap or a refusal, which the #95 confirmation step already provides.
2. On desktop, EdgeTAM followed the plate as well as SAM 2.1 on 24 of 25 clips; nothing was run on a phone. It
   needs an identity check after any loss:
   re-detection against the seed, or a check that position, radius and motion stay consistent, before a re-acquired
   track is trusted. Until then, frames after a loss should count as lost.
3. Neither tracker's mask gives the scale. A full-resolution rim fit (ellipse on sub-pixel edge points) is the
   likely route for centre and radius, with the tracker only saying where the plate is.
4. Next measurements: EdgeTAM timings on the reference phone (Xiaomi 14T Pro, Dimensity 9300+, ADR-0009) through
   LiteRT, and a frozen run of the chosen seed and tracker on new independent sessions
   ([next-round plan](../plans/PLATE_SELECTION_NEXT_ROUND_PLAN.md)).

## Reproduce

Same venv and checkpoint cache as Phase A, with `HF_HUB_CACHE` pointing to `validation/private/models/hf` and
`HF_HUB_OFFLINE=1`. Both runs used torch 2.8.0+cu126; the requirements now pin torch 2.14.1 (#241), so a rerun from
the current pins is a different environment. The score files were produced by the scorer at 8899977, which takes
the refusal constants from each run's `run.json`. From a worktree, with private data in the main checkout:

```bash
python research/plate-detection/run_phase_a2.py --repo-root <main checkout> --seed sam3 --output-dir validation/private/plate-detection-sam3-seed/run-2
python research/plate-detection/run_phase_a2.py --repo-root <main checkout> --seed sam3 --tracker edgetam --output-dir validation/private/plate-detection-sam3-seed/run-edgetam-1
python research/plate-detection/score_phase_a.py --run-dir validation/private/plate-detection-sam3-seed/run-edgetam-1 --repo-root <main checkout> --output validation/private/plate-detection-sam3-seed/run-edgetam-1/score.json --compare validation/private/plate-detection-sam3-seed/run-2/score.json
python research/plate-detection/apply_guard.py --run-dir validation/private/plate-detection-sam3-seed/run-edgetam-1 --output-dir validation/private/plate-detection-sam3-seed/run-edgetam-1-guard-v2
```

Run records (private), SHA-256:

| File | SHA-256 | Code |
| --- | --- | --- |
| `run-2/run.json` | `088aa7ba6aec408d5b72aa147cf0cc77b42312095c76b82719fcb82c6e35742b` | 4c2d531 |
| `run-2/score.json` | `cf229ff215f76d876a67b82cc87f62f5667c4f073e535cedc6ea4fee12a7eed4` | 8899977 |
| `run-edgetam-1/run.json` | `b7b3bd0eafa2076cf16a774f39cd42e4d5c8c729de1e5c7e3106e7a45f047f8e` | d0caa1b |
| `run-edgetam-1/score.json` | `3897cf194680df8048590c7a430bc0ec57605dd3ca7ff3cb28f4236c7deed337` | 8899977 |
| `run-2-guard-v2/run.json` | `7bb164bad25622f461dd656160baf3799b4e244d6d951c5abcb022112c172c94` | 4923a66 |
| `run-2-guard-v2/score.json` | `2e090a76bb387417e15324dfca191eec8091dcc67792d469eaca79c13cea4614` | 4923a66 |
| `run-edgetam-1-guard-v2/run.json` | `47e2231db031b7c6bd4606ee0207ccac73f3ad3091d5c6a14f58f5cf83781cc7` | 4923a66 |
| `run-edgetam-1-guard-v2/score.json` | `975986ef189294396ee4feac24d14fd18a92156b092894f3c869674e4ccca17e` | 4923a66 |
