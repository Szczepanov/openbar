# Open-vocabulary plate detection, Phase A.2: detect once, then track (#233)

Date: 2026-10-17. Research only, development clips only: nothing here is a frozen or held-out result, and no
number below generalises beyond these clips. Approval and scope: [#233](https://github.com/Szczepanov/openbar/issues/233),
tracker [#234](https://github.com/Szczepanov/openbar/issues/234). Builds on
[Phase A](PLATE_DETECTION_PHASE_A_RESULTS.md); code in `research/plate-detection/` (`track.py`, `run_phase_a2.py`).

## Question

Does choosing the plate once, on the label-free frame 0, and propagating it with SAM 2.1 video tracking hold the
plate's identity across a clip better than per-frame detection (GroundingDINO + SAM 2.1, plausible top-1 on every
frame, 320/388 labelled frames in Phase A)?

## Summary

- **Tracking does not fix identity; it inherits the frame-0 choice.** Labelled frames on target: **276/388** for
  `track_sam2`, against **320/388** for per-frame `gdino_sam2`.
- **When the seed is right, tracking is better.** On the 22 clips where the frame-0 seed is the labelled plate,
  tracking keeps it on every labelled frame: 276/276, against 266/276 for per-frame detection on the same clips.
  The gain is mostly `self-snatch-side-0212-24` (28/28 against 18/28). All 22 clips are on target throughout,
  against 21/25 clips for per-frame detection.
- **When the seed is wrong, every frame is wrong.** Three clips start on the wrong plate and stay there: 0/25, 0/15
  and 0/72 labelled frames, where per-frame detection still recovers 10/25, 10/15 and 34/72.
- **The tracker cannot tell it is on the wrong plate.** SAM 2.1's object score stays at about 0.9999 on the wrong
  plate too. A bad seed has to be caught at the seed: by a refusal, by a human, or by a later check against a second
  detection.
- **Refusal (`close-score-v1`, runner-up at least 0.9 × best).** On frame 0 it refuses 1 clip for `gdino_sam2` and
  `track_sam2` (`self-back-squat-side-1015-06`, which would have been wrong), leaving 18/19 accepted clips correct.
  It misses `self-other-side-0215-27` (ratio 0.80). For Hough it refuses 4 clips, 2 of which would have been correct.
- **Cost.** Median 0.33 s per tracked frame on an RTX 3060 Ti, including the rim fit. The frame-0 detection adds
  about 1 s once per clip.

## Method

| Step | What happens |
| --- | --- |
| Decision frame | The first decoded frame (frame 0). Labels never choose it. |
| Seed | `gdino_sam2` exactly as in Phase A on frame 0, then `selection.plausible` (baseline size window, unchanged) and `select_top1` (`track.choose_seed`) |
| Track | `Sam2VideoModel` / `Sam2VideoProcessor`, `facebook/sam2.1-hiera-base-plus` (same checkpoint as the seed refiner), streaming mode, float32. The seed's SAM 2.1 image mask is the frame-0 prompt. |
| Frames | Every 2nd decoded frame from frame 0 (`clips.TRACK_STRIDE = 2`: about 30 fps on 60 fps clips, 15 fps on 30 fps clips), plus the frame nearest each labelled time so that every label can be scored. Labels add frames only. 11,719 frames over 27 clips. |
| Circle | `geometry.circle_from_mask` on each tracked mask, unchanged from Phase A. An empty mask is a lost frame with no candidate; nothing is interpolated. |
| Score | Sigmoid of SAM 2.1's object-score logit. Specific to this tracker, not a calibrated probability, and not comparable with detector scores. |
| Refusal | `close-score-v1` over the plausible frame-0 detector candidates: refuse when the second-best score is at least `REFUSAL_SCORE_RATIO = 0.9` × the best. Refused clips are still tracked and reported separately. |

Session memory is bounded: after each frame, frames and non-conditioning outputs older than the model's memory window
(`max(num_maskmem, max_object_pointers_in_encoder) + 1` frames) are dropped. The model never reads them, so the output
does not change.

Scoring (`score_phase_a.py`, extended): the same `identity-loose-v1` match and the same 388 labelled frames on 25
clips as Phase A. A `track_sam2` frame holds at most one candidate, so frame recall, top-1 and plausible top-1
coincide. Its frame-0 choice and refusal are read from the seed candidates stored on frame 0. New counts: frame-0
refusals, frame identity over non-refused clips, clips with every labelled frame on target, and lost frames.

## Results

| Method | Frame plausible top-1 | Clips with every labelled frame on target | Frame-0 plausible top-1 | Frame-0 refused (would be correct) | Frame-0 accepted, correct | Frames on target, non-refused clips | Radius err, median | Latency per frame, median |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| hough (Phase A) | 130/388 | 8/25 | 13/20 | 4 (2) | 11/16 | 98/200 | 0.029 | 0.072 s |
| gdino_box (Phase A) | 265/388 | 12/25 | 18/20 | 2 (0) | 18/18 | 248/301 | 0.049 | 0.335 s |
| gdino_sam2 (Phase A) | **320/388** | 21/25 | 18/20 | 1 (0) | 18/19 | **310/373** | 0.014 | 0.555 s |
| track_sam2 | 276/388 | **22/25** | 18/20 | 1 (0) | 18/19 | 276/373 | **0.013** | **0.328 s** |

Phase A rows are the Phase A run-1 candidates rescored with the extended scorer; the existing Phase A numbers
reproduce exactly. Lost frames: 36/11,719 for `track_sam2`, all on `self-other-side-0215-27`, where the tracked
wrong plate leaves the frame. No Phase A method has lost frames by construction.

Per clip (labelled frames on target; frame-0 outcome `ok` / `miss`, or `-` with no frame-0 label; `R` = refused):

| Clip | gdino_sam2 | track_sam2 |
| --- | --- | --- |
| self-back-squat-side-002 | 10/25 - | 0/25 - |
| self-back-squat-side-1015-06 | 10/15 miss R | 0/15 miss R |
| self-back-squat-side-1015-09 | 22/22 ok | 22/22 ok |
| self-back-squat-side-1015-10 | 25/25 ok | 25/25 ok |
| self-clean-jerk-side-002 | 21/21 - | 21/21 - |
| self-clean-side-0203-17 | 29/29 - | 29/29 - |
| self-hang-snatch-side-002 | 1/1 - | 1/1 - |
| self-other-side-0215-27 | 34/72 miss | 0/72 miss |
| self-snatch-side-002 | 23/23 - | 23/23 - |
| self-snatch-side-0206-19 | 29/29 ok | 29/29 ok |
| self-snatch-side-0212-24 | 18/28 ok | **28/28 ok** |
| self-snatch-side-0213-25 | 22/22 ok | 22/22 ok |
| self-snatch-side-1015-01 … 05 (5 clips) | 68/68 ok | 68/68 ok |
| vbt-* (8 clips, one label each) | 8/8 ok | 8/8 ok |

## Failure review (seed candidates and tracked circles, private run files)

- **`self-back-squat-side-002`, wrong plate at frame 0.** Its labels start at 4.80 s, so frame 0 has no label. On
  frame 0 the plausible top-1 (0.36) is a plate at about (578, 868), roughly 430 px from where the labelled plate
  sits at 4.80 s (322, 491). The runner-up, at (400, 552), scores 0.31 (ratio 0.85, below the refusal ratio). The
  tracker holds the first plate for the whole clip; per-frame detection still picks the labelled plate on 10/25
  labelled frames.
- **`self-back-squat-side-1015-06`, stored plate outscores the target** (0.43 against 0.41, the Phase A failure).
  Refused by `close-score-v1`; tracked anyway for the record, it stays on the stored plate.
- **`self-other-side-0215-27`, ambiguous target.** The seed is a circle centred off the left frame edge (x ≈ −23 px),
  a partly visible plate; the labelled plate on a racked bar in the background is not among the plausible
  candidates. The size window does not check whether the centre lies in the frame. Not refused (ratio 0.80).
- **`self-snatch-side-0212-24`, where tracking helps.** Per-frame plausible top-1 misses the labelled plate on 10
  of 28 labelled frames; the tracker keeps the correct seed on all 28.

## Limits

- 25 scored clips from few sessions; three clips decide the headline difference. Development estimates only.
- The refusal ratio 0.9 was fixed before any A.2 result existed, but after reading the Phase A failure review,
  which reports the 0.43 against 0.41 case that it now refuses. Treat the refusal's one correct catch as optimistic.
  It is not tuned further here.
- Frame-0 identity is scored on the 20 clips with a frame-0 label. Seed errors on clips without one (such as
  `self-back-squat-side-002`) show up only in the frame columns.
- Stride 2 halves the frame rate; a faster lift or a lower frame rate might lose the plate where this run did not.
  Whether a different stride changes identity was not tested.
- The tracker score is not a confidence that the identity is right: it was about 0.9999 on all three wrong seeds.
- GPU inference is not bit-for-bit reproducible across GPUs or library versions; `run.json` records packages,
  checkpoint revisions, weight hashes and per-file source hashes.
- No candidate enters canonical `Analysis` or a #95/#113 session record; all candidates are machine-origin.

## What this suggests (no decision taken)

1. Detect once, then track, is the better trajectory source **after** the identity is right. Seed errors decide its
   score, so the seed step needs a check: a human confirmation of the seed (as #95 already does), a refusal, or
   periodic re-detection that flags a disagreement between the tracked plate and a fresh detection.
2. A cheap seed-side check this run points at, still untested: reject plausible candidates whose centre lies
   outside the frame (`self-other-side-0215-27`).
3. Any rule taken forward is frozen first and scored once on new sessions
   ([next-round plan](../plans/PLATE_SELECTION_NEXT_ROUND_PLAN.md)); these development numbers do not count.

## Reproduce

Same venv and checkpoint cache as Phase A (`research/plate-detection/requirements.txt`, `HF_HUB_CACHE` pointing to
`validation/private/models/hf`). From a worktree, with private data in the main checkout:

```bash
python research/plate-detection/run_phase_a2.py --repo-root <main checkout> --output-dir validation/private/plate-detection-phase-a2/run-2
python research/plate-detection/score_phase_a.py --run-dir validation/private/plate-detection-phase-a2/run-2 --repo-root <main checkout> --output validation/private/plate-detection-phase-a2/run-2/score.json --compare <Phase A score.json>
```

Run record (private): `run.json` SHA-256 `b2d51f3758ac4d3839fc8bf74001953c2b9b77c34da59e5f2d389f197b9c8999`, code
commit `d8f8ee7` (clean), on the pre-merge #235 branch; this PR's commits are that work rebased onto the merged #235.
The merge added one change on the seed path: `detectors.dedupe` now drops non-finite or zero-size boxes before NMS.
That leaves the output for valid boxes unchanged, so the run's per-file source hashes differ from the rebased
`detectors.py` only by that filter. Scores: `score.json` SHA-256
`36b3bd4dff30de5573075fccd9b5a380a413d2c83f3d4060a01d142b34531606`; the Phase A rescore:
`4113bfa81b03ad458f6917ba3fc6a8d70003c95feafd4a673c5d8295ae09b03d`. An earlier `run-1` was abandoned
unscored after 2 clips: a GPU-sharing slowdown, then the provenance guard correctly refused to resume it under a
newer commit.
