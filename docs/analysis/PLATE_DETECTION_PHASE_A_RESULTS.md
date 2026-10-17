# Open-vocabulary plate detection, Phase A: development result (#233)

Date: 2026-10-17. Research only. Development clips only: nothing here is a frozen or held-out result, and
no number below generalises beyond these clips. Approval, scope and model licences:
[#233](https://github.com/Szczepanov/openbar/issues/233). Code: `research/plate-detection/`.

## Summary

- **Detection.** Text-prompted GroundingDINO finds the labelled plate far more often than the
  `plate-hough-edge-v1` baseline. On the decision frame it is among the candidates on 24/25 clips (Hough 20/25);
  over all labelled frames, GroundingDINO + SAM 2.1 finds it on 354/388 frames (Hough 258/388).
- **Selection.** Raw top-1 is no better than Hough (15/25): for "barbell plate" the detector's top box is often
  something long and thin, such as the yellow reference stick, whose fitted circle is larger than the frame allows.
  Dropping circles outside the baseline's own plate-size window (`PlateParams` radius 4–30% of the shorter side,
  reused unchanged) lifts top-1 to **23/25** for both GroundingDINO variants (Hough 15/25). With a label-free
  decision frame (frame 0, the 20 clips labelled there) it is **18/20** (Hough 13/20). Per labelled frame: 320/388
  for GroundingDINO + SAM 2.1 (Hough 130/388).
- **Geometry.** SAM 2.1 masks with a robust rim fit give a median radius error of 1.4% of the true radius
  (box only 4.9%, Hough 2.9%). The median centre error is 2.6% of the radius.
- **Cost.** Median 0.56 s per frame for GroundingDINO + SAM 2.1 on an RTX 3060 Ti (box only 0.34 s, Hough 0.07 s).
- **SAM 3** (run after access was granted, same frames and rules) is the strongest method: raw top-1 already selects
  the labelled plate on **24/25** clips (19/20 on frame 0) with no size window needed, and it finds the plate on
  375/388 labelled frames. Its only miss is the ambiguous two-target clip. Median radius error 1.6%, 0.72 s per
  frame. See [SAM 3](#sam-3).
- **The two selection rules designed against H1/H2 do not help here.** For GroundingDINO + SAM 2.1,
  `largest-plausible-v1` gives 19/25 and `motion-v1` 13/25 (19/25 after the size window), against 23/25 for
  plausible top-1. Box only: 17/25, 19/25 and 20/25. Hough: 15/25 and 8/25.

## Inputs

27 development clips (`clips.py`): 14 `split-exploration` clips (6 `self-*` and the 8 `vbt-*`) and the 13 clips
scored once by the frozen baseline (#231), now development-only for #178 and #57 (`PLATE_SELECTION_SPLIT.md`, rule
3 of the shared-pool amendment). The guard refuses `split-reserved-57` first and refuses everything else, including
the unexposed pilot and refusal-candidate clips, which stay unseen for a later frozen split.

Labels, read only by the scorer: frozen-eval label CSVs (pass a, with adjudicated rows applied), `annotation-v1`
files and manual seeds. 25 clips have labels with a radius. `self-snatch-ohs-side-001` and
`self-snatch-unknown-0219-28` have none and are not scored.

Frames (`clips.frame_plan`): every labelled time plus a 0.5 s grid (at most 40 grid frames per clip), decoded by the
existing PTS-checked `label_package` path. Labelled times choose which frames are decoded; proposers see only pixels.

Two decision frames are reported:

- **First labelled frame** (main columns). For 20 clips this is frame 0. For 5 it was chosen by a label:
  `self-back-squat-side-002` (4.80 s), `self-clean-jerk-side-002` (1.90 s), `self-snatch-side-002` (1.91 s) and
  `self-hang-snatch-side-002` (21.24 s) start at their seed frame, and `self-clean-side-0203-17` (0.99 s) has no
  labelled plate at 0 s. A label-free system cannot choose those frames, so these columns are optimistic.
- **Frame 0** (label-free), scored on the 20 clips with a label there.

Adjudicated label rows replace pass-a rows, and a non-labelled adjudication removes the point. The freeze record's
`unresolved` flag (set for `self-snatch-side-0213-25`) is not read; that clip is scored on its adjudicated geometry.

## Methods

| Method | What it does |
| --- | --- |
| `hough` | Unchanged baseline ranking (`hough_observe`), score = edge support |
| `gdino_box` | `IDEA-Research/grounding-dino-base`, prompts "weight plate." and "barbell plate.", box and text thresholds 0.20 (low on purpose, to measure recall), NMS at IoU 0.7 across prompts, circle inscribed in the larger box side |
| `gdino_sam2` | As `gdino_box`, then each box prompts `facebook/sam2.1-hiera-base-plus`; the circle is a seeded RANSAC plus least-squares fit to the mask boundary, ignoring pixels on the frame edge |

Selection rules (no labels): `top1` (highest score); `plausible_top1` (top-1 after the size window);
`largest_plausible` (largest plausible candidate scoring at least half of the best); `motion` / `plausible_motion`
(least persistent plausible candidate: stationary clutter keeps its spot across the clip, the lifted plate leaves).

Identity match for scoring (`identity-loose-v1`): centre within 25% of the true radius and radius within 25%. This is
deliberately loose and is not the frozen evaluation's Tc/Tr tolerance.

## Results

| Method | Target on decision frame | top1 | plausible top1 | largest plausible | motion | plausible motion | Frame-0 plausible top1 | Frame recall | Frame plausible top-1 | Centre err / r, median (p90) | Radius err, median (p90) | Latency per frame, median (p90) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| hough | 20/25 | 15/25 | 15/25 | 15/25 | 8/25 | 8/25 | 13/20 | 258/388 | 130/388 | 0.045 (0.151) | 0.029 (0.087) | 0.072 s (0.221 s) |
| gdino_box | 24/25 | 14/25 | **23/25** | 17/25 | 19/25 | 20/25 | **18/20** | 326/388 | 265/388 | 0.028 (0.134) | 0.049 (0.114) | 0.335 s (0.381 s) |
| gdino_sam2 | 24/25 | 15/25 | **23/25** | 19/25 | 13/25 | 19/25 | **18/20** | 354/388 | **320/388** | 0.026 (0.182) | **0.014** (0.056) | 0.555 s (0.642 s) |
| sam3 | 24/25 | **24/25** | **24/25** | 20/25 | 17/25 | 18/25 | **19/20** | **375/388** | 316/388 | 0.027 (0.150) | 0.016 (0.067) | 0.721 s (0.915 s) |

Geometry columns are over frames where the labelled plate is among the candidates (best-matching candidate),
relative to the true radius. Frame columns pool the 388 labelled frames, so clips weigh unequally:
`self-other-side-0215-27` alone contributes 72 frames and each `vbt-*` clip one. Clip columns count each clip once.

By clip group (`gdino_sam2`, plausible top-1): exploration 12/12, scored-development-only 11/13. Hough: 10/12 and
5/13. Hough's misses concentrate on the 2026-10-15 snatch session, where the plate is never its top-1. At frame 0
both GroundingDINO variants miss the same two clips as on the first labelled frame (below).

## SAM 3

Run separately once Meta granted access, into its own run directory, with the committed harness (5135548, identical
to the merged #235). Checkpoint `facebook/sam3` revision `3c879f39`, bfloat16, prompts "weight plate" and "barbell
plate", score threshold 0.30, mask threshold 0.50, the same NMS and rim fit as `gdino_sam2`.

- **Selection.** Raw top-1 is right on 24/25 clips and on 19/20 at frame 0; the size window changes nothing. SAM 3
  ranks the plate on the bar above the stored floor plate in `self-back-squat-side-1015-06` (score 0.95 vs 0.87 on
  frame 0), which both GroundingDINO variants got wrong.
- **Remaining miss.** `self-other-side-0215-27`, the ambiguous two-target scene: no candidate matches the labelled
  background plate on the decision frame.
- **Per frame.** Recall 375/388 (GroundingDINO + SAM 2.1: 354/388). Plausible top-1 per frame is 316/388, about the
  same as `gdino_sam2` (320/388): SAM 3 proposes more circles per frame (median 7 against 4) and outside the
  decision frame another candidate outranks the target on some frames (top-1 is right on 299 of the 375 frames
  where the target is a candidate).
- **Geometry and cost.** Median radius error 1.6% (p90 6.7%), centre error 2.7% of the radius. Median 0.72 s per
  frame for both prompts together; for the first part of the run the GPU was shared with another session's SAM 2
  tracking job, so this is an upper estimate.
- **Licence.** Meta's SAM License, not Apache-2.0: fine for this research, but anything shipped with it needs the
  separate licence review that #233 requires.

Run record (private): `run.json` SHA-256 `2f3ca5ee806f51423ff614ba268b5c5d4d324116f8ef069f47230aaf0925cc20`,
`score.json` SHA-256 `61f419e7b0e8ad90289d09fa1ebb79f1297113ebe32e3746de158dac2024edf9`.

## Failure review (decision-frame overlays, private, `overlay.py`)

- **Implausible top boxes.** On all 8 `vbt-*` clips the top GroundingDINO box for "barbell plate" is a tall, narrow
  strip over the yellow reference stick (about 155–180 px wide and 1350 px tall on a 1080 × 1920 frame). The
  box-inscribed circle takes the longer side (r = 673–686 px); the SAM 2.1 mask fit gives r = 920–1435 px, larger
  than the frame. The real plate is among the other candidates on every one of these clips. No aspect-ratio check
  exists; the size window is what removes these boxes.
  The size window removes these boxes, which is the whole gain from 15/25 to 23/25.
- **Stored plate outscores the target** (`self-back-squat-side-1015-06`): a plate lying on the floor scores 0.43
  against 0.41 for the plate on the bar. `largest_plausible` and `motion` both pick the right plate here, but they
  fail on other clips, so no rule tried is better overall.
- **Ambiguous target** (`self-other-side-0215-27`): the labelled plate is on a racked bar in the background while a
  person in the foreground holds a nearer plate. No candidate matches on the decision frame. This is an identity
  question for a human or a refusal, not a ranking fix.
- **Why the motion rule fails.** Racked and paused bars keep their spot for much of the clip. Many clips also have
  one plausible candidate, which leaves the rule nothing to choose between. Persistence over a 0.5 s grid cannot tell
  a resting target from clutter.

## Limits

- 25 scored clips from few sessions, mostly one barn gym; selection is counted once per clip. On 5 clips the first
  labelled frame was chosen by a human (see Inputs); the frame-0 column avoids that. The agent chose the size-window
  rule after looking at these clips' failures, although it reuses existing constants; treat 23/25 and 18/20 as
  development estimates with optimistic bias.
- The 13 scored clips are allowed by a hardcoded list in `clips.py`; a unit test checks it equals the clip ids named
  in `PLATE_SELECTION_BASELINE_REPORT.md`.
- When fewer than 12 boundary points survive RANSAC, the rim fit falls back to all boundary points but still
  records `fit = "mask_contour"`.
- `PLATE_SELECTION_SPLIT.md` lists 13 `split-exploration` clips; the private manifest tags 14 (it adds
  `self-snatch-unknown-0219-28`, which has no labels and is unscored here). The split doc needs an owner update.
- `identity-loose-v1` decides "same plate", not measurement accuracy. Radius error at the decision frame drives
  calibration scale; a 1.4% median is not the same as the ±0.5% scale agreement a VBT number needs.
- GPU timings on a desktop card say nothing about phones (#80). Inference is not bit-for-bit deterministic across
  GPUs or library versions; `run.json` records packages, checkpoint revisions and weight hashes.
- No candidate output enters canonical `Analysis` or a #95/#113 session record. All candidates are machine-origin.

## Next steps

1. **SAM 3** is done (above). It is the best zero-shot proposer here; a SAM 3-based candidate, or SAM 3 as the
   label source for Phase B, is the natural next freeze candidate, subject to its licence review.
2. **Detect once, then track** (Phase A.2): take the plausible top-1 on a decision frame and propagate it with SAM 2
   video tracking. This yields one identity per clip and a full trajectory, and tests whether
   tracking improves frame-level identity over 320/388.
3. **Recall on small or ambiguous plates**: prompt variants and tiling for distant plates, and an explicit refusal
   when two plausible plates are close in score.
4. **Phase B (needs a new owner decision)**: distil GroundingDINO + SAM 2.1 labels, with owner spot checks, into a
   small Apache-licensed detector (RF-DETR-Nano or D-FINE-S) for speed and the phone path.
5. Any candidate taken forward is frozen and scored once on new independent sessions
   ([next-round plan](../plans/PLATE_SELECTION_NEXT_ROUND_PLAN.md)); these development numbers do not count.

## Reproduce

Venv and pins: `research/plate-detection/requirements.txt`. Checkpoints cached under
`validation/private/models/hf` via `HF_HUB_CACHE`. From a worktree, with private data in the main checkout:

```bash
python research/plate-detection/run_phase_a.py --repo-root <main checkout> --methods hough gdino_box gdino_sam2 --output-dir validation/private/plate-detection-phase-a/run-1
python research/plate-detection/score_phase_a.py --run-dir validation/private/plate-detection-phase-a/run-1 --repo-root <main checkout> --output validation/private/plate-detection-phase-a/run-1/score.json
python research/plate-detection/overlay.py --repo-root <main checkout> --run-dir validation/private/plate-detection-phase-a/run-1 <clip id> ...
```

A rerun into an existing run directory must have identical provenance (methods, checkpoints, code, packages) or
`run_phase_a.py` refuses; the scorer refuses candidates for clips not listed in `run.json`.

Run record (private): `run.json` SHA-256 `574f6b402e14f43e94e479fd08ae87ec277863c9ba984294ba0c56d828b6369c`
(code commit e0b2df4 plus the uncommitted harness; per-file source hashes inside). Detection sources at run time
match this PR's `clips.py` and `geometry.py`. `detectors.py` later gained an index-based cross-prompt NMS that also
drops non-finite or zero-area boxes, and passes `dtype=` instead of the deprecated `torch_dtype=` for SAM 3.
Rerunning `vbt-017cea944dfbb194` with the new code gave candidates identical to run-1 on all 28 frames for both
GroundingDINO methods (latency aside). After the run, `selection.py` and `score_phase_a.py`
gained the size-window, largest and frame-0 rules, `truth.py` the adjudicated-removal rule (no current adjudicated
row is non-labelled), and `run_phase_a.py` the rerun guard; none of these touch candidate generation. Scores:
`score.json` SHA-256 `62c36ff01a46521d5715bbe8b42385e1786c72ee868304d2191c5f1a6b8b72a2`. Checkpoints:
`IDEA-Research/grounding-dino-base` revision `12bdfa31`, `facebook/sam2.1-hiera-base-plus` revision `b7320756`
(its `sam2.1_hiera_base_plus.pt` matches the #57 pinned SHA-256 `a2345aed…`). Packages: torch 2.8.0+cu126,
transformers 5.10.4, numpy 2.2.6, OpenCV 4.12.0.
