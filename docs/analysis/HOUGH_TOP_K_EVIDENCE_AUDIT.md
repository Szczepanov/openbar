# Hough top-K target recall: evidence audit

Date: 2026-10-15. Request: automatic barbell/plate research, Prompt 1 only.
Audited latest `origin/main`: `c64011636e9312e01f810b243b84fedc99cc4a3d`.

## Result: C — insufficient evidence

Neither proposal-generation failure nor semantic-ranking failure is established.
No candidate run, scoring, parameter tuning or media inspection was performed.
Top-K recall and score-margin usefulness are **not measurable from the available
independent evidence**. This is an evidence audit, not a completed experiment or
a new scope decision. Do not advance to Prompt 2 or a learned detector from it.

Two prerequisites are absent:

- The [automatic-selection plan](../plans/AUTOMATIC_PLATE_SELECTION_PLAN.md)
  retains DEFER. On the audit date, [#178](https://github.com/Szczepanov/openbar/issues/178)
  was closed with zero comments; its body still gates implementation on an explicit
  scope decision. No PROCEED was recorded there. This request supplies the research
  questions but does not record the plan's required decision and bounded run terms.
- The [split record](../validation/PLATE_SELECTION_SPLIT.md) reports zero independent
  frozen-evaluation clips. This checkout has no `validation/private/frozen-eval/`
  directory; its private annotations/seeds directories contain only `.gitkeep`.
  This local absence does not prove that labels exist nowhere else.

The split document inventories 13 exploration clips and six reserved clips; these
are documented inventory counts, not cases inspected or scored in this audit.
The eight historical VBT clips were adjusted after proposal exposure, and their
target-identity/rejection labels are explicitly not derivable in the
[retained evaluation](VBT_SUGGESTION_EVALUATION_RESULTS.md). Existing manual seeds,
SAM/tracker outputs and proposal acceptance cannot supply the missing independent
outer-rim identity reference. No reserved clip was opened, decoded or scored.

## What the implementation actually does

Source: `research/vbt-workflow/vbt_suggest.py`, `PlateParams`, `_plate_features`,
`edge_support`, `_fits`, `_refine`, `suggest_plate` at the audited commit.

1. Blur grayscale pixels and run Hough on a copy with maximum side 960 px.
   Radius limits are 0.04–0.30 of the shorter display side; Hough thresholds are
   120/25 and minimum centre distance is 0.05 of the shorter side (minimum 4 px
   after scaling).
2. Take only the **first 60 Hough outputs before edge-support ranking**. Convert
   to display pixel-centre coordinates and discard circles that do not fit fully
   inside the frame. Thus the retained set is not every Hough output.
3. Score 360 rim samples using Canny-distance tolerance 2 px and radial-gradient
   alignment at least 0.7. Sort by descending support, then ascending x, y, radius.
4. Refine only the first candidate with a different 1.5 px support tolerance.
   Require refined support at least 0.4 and fitting geometry, including after
   rounding. Failure does not fall back to candidate #2.

The pre-refinement ranked list is local and is discarded. The returned confidence
is rounded **refined** support, not the unrefined ranking score or a calibrated
identity probability. A difference between returned confidences cannot recover
the missing candidate #1/#2 margin. Hough can produce concentric/distractor circles;
the implementation has no semantic identity test.

A future observation-only harness should retain the actual local list before
`_refine`, without replacing the implementation or changing its defaults. Keep
separate counts for total Hough output, first-60 truncation, fit rejection and
ranked survivors. Record raw-order indices, unrounded display geometry and support,
plus the existing final selection/refusal separately. This distinguishes a raw
Hough miss from a truncation/bounds exclusion, ranking failure or refinement failure.
No such harness was implemented while the plan remains DEFER.

## Requested outputs and denominators

| Output | Audit result |
| --- | --- |
| Raw per-case results | No scored cases; no candidate or label rows manufactured |
| Top-1 / top-3 / top-5 / top-10 / top-20 recall | All unavailable; eligible independently labelled denominator = 0 in this checkout |
| Candidate counts, true-target rank, geometry and support | Unavailable without an authorized candidate run and independent matching labels |
| Best-minus-second-best margin | Unavailable; existing returned suggestion omits the ranked set |
| Wrong-top-1 taxonomy and Hough misses | Unknown, not zero |
| Lift, size, blur, occlusion, crop, concentric-rim, contrast and distractor strata | No independently scored strata |
| Uncertainty | No rate or Wilson interval for a zero denominator; session generalization unmeasured |

For a future frozen run, count all independently selectable cases in each top-K
denominator, including cases with no candidates. If fewer than K candidates exist,
test membership in all available candidates; do not drop the case. Report candidate
availability separately. Required-refusal and indeterminate cases need separate
counts and cannot earn positive target recall. Candidate runtime errors remain
visible and cannot be removed to improve recall.

For score-margin analysis, use unrounded pre-refinement score #1 minus #2, retaining
ties as zero and fewer-than-two candidates as unavailable. Freeze a descriptive
analysis before scoring, relating margins to independently labelled wrong-top-1
and required-refusal outcomes. Do not select an acceptance threshold on those results.
Absent labels and margins, no claim about ambiguity prediction is possible here.

## Additional annotation required before the experiment

Use independently collected recording/session groups and the existing challenge
matrix, including negative, ambiguous and unsupported scenes. Exclude #57 reserved
and formal #79 inputs; do not reassign frozen roles. Exploration footage can support
debugging but cannot become unseen evaluation evidence.

Before displaying any candidate overlays or scores, retain two independent label
passes and adjudication (or unresolved status) for each evaluation case:

- Source/session identity, source hash, exact display-oriented frame and PTS,
  movement onset/uncertainty, target visibility/selectability and expected refusal
  policy/reason.
- Unique intended lifted-barbell camera-facing outermost plate/rim identity;
  centre and outer radius with repeatability/uncertainty. Distinguish concentric
  rims and other objects even when centres coincide. An unresolved target remains
  indeterminate rather than being assigned the nearest candidate.
- Lift and the requested visual challenge attributes. For wrong-top-ranked circles,
  a blinded scene/object inventory should support later adjudication as another
  plate, another barbell, rack/storage, background circle, wrong concentric rim or
  other. Unresolvable distractor category stays unknown.

Before scoring, freeze the candidate-membership rule (identity **and** centre/radius
tolerances informed by independent repeatability), annotation reconciliation,
frame schedule, exclusions, denominator rules and descriptive margin analysis.
No numerical match tolerance or "very high recall" threshold is chosen here.
Bind code/configuration/input/label hashes and commands in the
[freeze record](../validation/PLATE_SELECTION_FREEZE_RECORD.md), after the owner's
required PROCEED decision specifying entry point, split, budget and stop criteria.

## Interpretation and next bounded step

Earlier [radial](M0_RADIAL_GEOMETRY_FAILURE_ANALYSIS.md),
[mask-guided rim](MASK_GUIDED_RIM_EXPERIMENT_RESULTS.md) and
[colour/hub](COLOUR_HUB_CUE_RESULTS.md) failures concern centre/rim evidence on
known-target ROIs. The [plate-scale study](PLATE_SCALE_STUDY_RESULTS.md) concerns
physical calibration. None measures full-frame intended-target top-K recall.
They cannot select between proposal generation and semantic ranking here.

The next step is independent annotation and authorization for **Prompt 1**, then
an observation-only run with unchanged Hough parameters. Compare paired top-1 and
top-K membership before recommending ranking work or a detector. Prompt 2 and all
later experiments remain unstarted; no architecture/M0/M1 promotion is justified.

## Reproduction and boundaries

Audit commands: `git fetch origin main`, `git rev-parse HEAD`,
`git rev-parse origin/main`, `gh api repos/Szczepanov/openbar/issues/178`,
`gh api repos/Szczepanov/openbar/issues/178/comments`, and
`gh api repos/Szczepanov/openbar/issues/178/timeline`.
Read the cited committed sources at the audited hash; use `Test-Path
validation/private/frozen-eval` and directory-name inventories for local availability.
GitHub state and private file availability are observations as of the audit date.

Verification: all eight relative document links resolve and the staged diff passes
`git diff --cached --check`. An independent read-only review checked implementation
facts, research-method constraints and denominator claims against the source/plan/split
documents; it found no actionable issues. Runtime suites were not run for this
report-only change. Neither review nor link checks establish selection accuracy.

Only this report changes. No model, dependency, external implementation, media,
canonical output, schema, fixture, method/version or production default is added
or changed. Licensing and deployment review for any future learned model remains
required. The held-out guard enforces purpose/tag exclusion, but it does not verify
owner authorization, annotation independence or preregistration; those gates still
need explicit evidence rather than a passing guard alone.
