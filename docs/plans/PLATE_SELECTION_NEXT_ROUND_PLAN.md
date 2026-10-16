# Plate selection: next evidence round (#178)

Date: 2026-10-16. Work type: **evidence preparation**; nothing here is a scored run or a new
candidate. Parent: [AUTOMATIC_PLATE_SELECTION_PLAN.md](AUTOMATIC_PLATE_SELECTION_PLAN.md).
Result it follows: [PLATE_SELECTION_BASELINE_REPORT.md](../validation/PLATE_SELECTION_BASELINE_REPORT.md).

## Context

The frozen baseline run selected the labelled plate on 0 of 10 selectable clips. The report
calls the stage attribution **insufficient evidence** for four reasons this plan addresses:

1. Tc/Tr came from the evaluation clips' own A/B gaps, not a source-separated pilot, and radius
   was never independently repeated.
2. There was no scene inventory, so every wrong target is class `unknown`.
3. No clip required a refusal, so unsafe acceptance was not estimable.
4. Two passes by one annotator measure repeatability, not independence.

The 13 scored clips are now **development-only** for any revised candidate (PLATE_SELECTION_SPLIT.md),
and the same holds for #57.

## Exploration diagnosis (2026-10-16, development clips only)

The unchanged baseline was run on the first frame of the 14 `split-exploration` clips with the
top-5 ranked circles drawn. These clips are proposal-exposed by design. No parameter was changed
and nothing was scored. Private overlays: `validation/private/exploration-diagnosis/baseline-frame0/`.

- The 8 `vbt-*` clips (one session, clean background, plate near the floor) all selected a
  plate-sized circle (r ≈ 176–185 px at 1080 px width, support 0.71–0.89); the one overlay
  inspected was on the plate.
- `self-back-squat-side-002`: the bar is racked high at frame 0. The top five are all in cable and
  chain clutter at the bottom of the frame; the racked plate is not among them.
- `self-snatch-unknown-0219-28`: low, blurred camera. Top-1 is a small dark object near the bar;
  the visible plate is rank 4.
- The other four `self-*` clips selected plate-sized circles.

Hypotheses only, to be tested on development clips, never on a frozen split:

- **H1 (clutter):** dense curved edges in barn clutter (cables, chains, plates stored on the floor)
  earn higher edge support than the target plate. That fits the five far misses in the frozen run
  (circles 51–82% smaller than the plate, 190–760 px away).
- **H2 (frame choice):** at frame 0 the target plate can be racked, partly out of view or blurred,
  so a single fixed frame understates what a short pre-movement search could find.

## Goal and non-goals

Goal: make the next frozen evaluation interpretable: a real pilot for Tc/Tr including radius, a
scene inventory, refusal cases, and enough independent sessions for useful intervals.

Not in scope: changing `vbt_suggest.py` or `PlateParams` in production code, any canonical
`Analysis` or schema change, models or new dependencies, M1 entry, an audit sampling rate, or any
use of the 13 scored clips as unseen evidence.

## Inventory of unexposed clips (from the private manifest)

| Pool | Clips | Sessions | Use |
| --- | --- | --- | --- |
| Pilot | `self-snatch-side-0203-16`, `self-back-squat-side-0203-18`, `self-other-side-0206-20`, `self-clean-side-0206-21`, `self-clean-side-0212-22`, `self-snatch-side-0212-23`, `self-other-side-0213-26` (`boundary`, side view) | 2023-02-03, -06, -12, -13 | Repeatability pilot. These sessions already hold scored clips, so they cannot be unseen evaluation again, but no algorithm output has been seen on these clips. |
| Refusal candidates | 14 `ood` clips (front, rear, oblique or unknown view) and `self-nolift-unknown-0226-29` | 2023-01-07 to 2023-02-26, excluding the sessions above | Candidate `refuse_unsupported` / `refuse_no_target` cases for the next frozen split, after the owner confirms each policy. |
| Not usable | the 2026-10-15 oblique and bench-press clips | 2026-10-15 | Same session as scored clips; development only. |
| Missing | new side-view clips that should be selected | new sessions | Must be recorded; existing footage cannot supply them. |

Selectable side-view clips from fresh sessions are the binding gap. Without them the next frozen
split could estimate refusal behaviour but not selection accuracy.

## Work packages

1. **Pilot labels (owner).** For each pilot clip, a single-frame `label_package --at-s 0` package,
   labelled twice blind with centre **and** a Shift+click on the outer rim for radius. Keep the
   files under `validation/private/frozen-eval/pilot/`. A second, independent annotator is
   preferred; if not available, say so in the record.
2. **Tc/Tr derivation (agent).** Nearest-rank 95th percentile of the pilot centre gaps and absolute
   radius gaps, exactly as HOUGH_TOP_K_PREPARATION.md specifies, written to the freeze record
   **unrounded**. (The baseline record froze 5.4 px for a 5.44 px value; one clip changed group.)
   Record pilot n, sessions and an adequacy note; if n < 7 or a tolerance is 0, matching is
   NOT READY.
3. **Scene inventory (tooling, agent; labels, owner).** Extend the label page with an optional
   per-frame inventory: for each other circular object at the decision frame, a class from the
   plan's taxonomy (another plate on the target bar, concentric rim, another loaded bar, stored
   plate, rack or equipment, background object, other). Store it beside the label CSV with its own
   hash. Research tooling only; no schema change.
4. **Refusal policy (owner).** Confirm `expected_policy` for each refusal candidate from its first
   frame, without candidate output.
5. **New recordings (owner).** At least 2 new sessions of side-view snatch, clean and back squat,
   ideally including one other gym or lighting. Tag them `split-frozen-eval` before anyone looks at
   proposals.
6. **Development work (agent, separate PR per idea).** Test H1 and H2 on exploration clips and on
   the 13 now-development clips: for example a support penalty for clutter-like texture or a short
   pre-movement search window. Each idea is a separately named candidate frozen before the next
   evaluation; none changes production `suggest_plate` without a later owner decision.
7. **Next freeze (agent, after 1–5).** New record citing the pilot Tc/Tr, inventory and refusal
   policies; the executor and scorer as they are (#229, #230), extended only where the record
   needs it (scene-inventory scoring).

## Gates and stop conditions

- No scored run without: pilot Tc/Tr with an adequacy note, owner-confirmed policies, and at least
  two independent sessions of selectable clips not seen by any candidate.
- Stop and report "insufficient independent evidence" if new sessions cannot be recorded.
- Any candidate changed after it sees a frozen split makes that split development-only.
- An owner decision on #178 is needed before the next scored run, as for the first.

## Verification

Tooling changes in packages 2, 3 and 7 follow the existing gates: `python -m unittest discover -s
research/vbt-workflow/tests -p 'test_*.py'`, `python -m unittest discover -v -s validation/tests
-p 'test_*.py'`, `python validation/tools/schema_check.py`, plus an independent review before any
real-data run.
