# Colour and hub cues for rim sampling: development result

Date: 2026-10-15. Result of the experiment registered in
[COLOUR_HUB_CUE_EXPERIMENT_PLAN.md](../plans/COLOUR_HUB_CUE_EXPERIMENT_PLAN.md) (SHA-256
`b157143113f0f70ae1cd3d7c403376ca11ddb6fa823c27253ed9a5085e14dda9`, commit 4c4213c). The owner recorded the
go-ahead in chat; the agent wrote it into the registration record at the owner's request.

## Decision

**REJECT all three arms (C, H, C+H).** No arm meets the registered rules. The plan's stop rule applies: one run
per arm, no threshold, band, template or model change, and no variants after this result.

## Data

Three development clips only (`self-back-squat-side-002`, `self-clean-jerk-side-002`, `self-snatch-side-002`).
No held-out, 2026-10-15 or 2023 clip was processed. Coarse centres: SAM 2.1 small-circle, regenerated once
and frozen (prediction SHA-256: squat `619866a269ba85ae473fdfd8a0d8af15684dc4e0af38a21b1e946162c9c0cbca`, clean-jerk
`e5860bb45a522e0cd79fac920507ade9d112405dc8a42eaefe54896d53a81970`, snatch `3ec80e6dc5099150313393ad032c89be7dda0da97eabdb587bdbd48c7c9f6fda`). The regenerated baseline matches the
historical one within 0.21 px (centre MAE on all labels 1.845 / 4.132 / 3.789 px against 1.922 / 4.338 / 3.961 px).
Arms are compared with the baseline of the same run. Evaluation file SHA-256: `0bdabbbe85f12895762704bd0d26da88fcdd066c3557609ac40f10cd963cd808`.

## Result per arm (seed excluded, common support with the baseline)

| Arm | Labelled coverage (squat / clean-jerk / snatch) | Centre MAE change vs baseline | Verdict |
| --- | --- | --- | --- |
| C colour-gated rim | 0% / 0% / 0% | unsupported (no matched labels) | rejected: rule 1 |
| H hub fused with SAM | 100% / 100% / 100% | -0.12 / +0.05 / -0.06 px (baseline 1.92 / 4.34 / 3.96) | rejected: rule 2, hub unsupported on snatch |
| C+H | 0% / 0% / 0% | unsupported | rejected: rule 1 |

- **C and C+H** produced a coordinate on none of the labelled frames. Over all frames of the clips the gated rim
  was accepted on 27 of 307 (squat), 0 of 637 (clean-jerk) and 0 of 700 (snatch). Almost every loss was
  `insufficient_rim_support` (median accepted edges 31, 24 and 13 of 72 rays against the required 44), the failure the
  earlier radial experiments already showed. The colour gate removed rays rather than fixing the rim: the median
  number of rays without a colour-eligible edge was 0, 4 and 42 per frame. The plate face is nearly neutral and dark,
  so its colour model (a and b scale about 1.5) is effectively a brightness test that true rim rays often fail under
  changing light and blur.
- **H** barely moves the centre: improvement of 0.12, -0.05 and 0.06 px against the 0.5 px required on two of three
  clips. The hub was accepted on 92%, 100% and 27% of labelled frames, so H is unsupported on the snatch clip (below
  the registered 50%). Displacement MAE changed from 2.00 to 2.01, 3.95 to 3.67 and 4.30 to 4.16 px, descriptive only
  and not a registered rule. Silent false tracks (error above 3 px at confidence 0.8 or more): 3 against 4, 11 against
  11 and 11 against 11.
- **Control** (colour model from another clip) is uninformative because the real arm already has no coverage.

## Rules and interpretations

Arm C failed rules 1, 2 and 3; C+H failed 1, 2, 3 and hub support; H failed rule 2 and hub support. Rules 4 (silent
false tracks) and 5 (no coordinate when every gate fails) were met by all three arms. The interpretations of the
plan's wording used for the rules are written in the header of `research/colour-hub-cue/evaluate_rules.py`, which was
committed with its tests before any arm result was read.

## Deviations and caveats

- After the first evaluation I corrected a bug in the rule-5 check (the manual-seed anchor frame was treated as a
  measurement) and re-ran the evaluation. No threshold or interpretation changed, and no verdict changed.
- The control arm's rule-5 flag on the snatch clip is an artefact of checking it against the real arm's diagnostics;
  it is not part of the rules.
- Three clips from one session, all with dark plates: this says nothing about coloured plates, other gyms or light.
- Label noise is about 1.4 to 3.1 px, so differences under about 0.5 px cannot be resolved.

## Consequence

Colour and hub cues, as specified, do not improve on SAM 2.1 small-circle on this footage and do not recover the rim.
Per the plan, no variant is tried. The unresolved bottleneck remains consistent rim evidence on dark plates; the next
useful evidence is independent, repeated labels and held-out clips through the existing #57 process, not further
tuning of these cues.
