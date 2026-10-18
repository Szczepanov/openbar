# Implementation plans

`docs/plans/` contains execution-oriented plans for work that has already been analyzed enough to
sequence concretely, but is not necessarily approved as a production architecture decision.

Use the repository documentation layers consistently:

- `docs/analysis/` — investigation, alternatives, external references and reasoning;
- `docs/plans/` — ordered implementation work packages, dependencies, tests, evidence and decision gates;
- `docs/validation/` — normative measurement/validation contracts;
- `docs/roadmap/` — milestone scope and sequencing;
- `docs/adr/` — durable architecture decisions.

A plan does not override an accepted ADR, schema, validation contract or scope fence.

## M1 preparation

- [Automatic plate selection (#178)](AUTOMATIC_PLATE_SELECTION_PLAN.md): #95/#113 reuse audit,
  proposed import/correction behavior and development evaluation protocol. Prototype implementation
  remains deferred beyond the owner's **PROCEED — Prompt 1 only** preparation decision.
  [Hough top-K preparation](HOUGH_TOP_K_PREPARATION.md) records the observation harness,
  independent annotation and preregistration protocol. The baseline was run and scored once on
  2026-10-16 ([report](../validation/PLATE_SELECTION_BASELINE_REPORT.md): 0/10, stage attribution
  insufficient evidence). [Next evidence round](PLATE_SELECTION_NEXT_ROUND_PLAN.md): pilot
  tolerances, scene inventory, refusal cases and new sessions. None of these plans authorizes M1
  entry or Prompt 2.
- [Open-vocabulary plate detection, Phase A (#233)](../analysis/PLATE_DETECTION_PHASE_A_RESULTS.md):
  owner-approved GroundingDINO, SAM 2.1 and SAM 3 on the 27 development clips.
  With the baseline's plate-size window, top-1 selects the labelled plate on 23/25 clips (Hough 15/25),
  or 18/20 with a label-free frame-0 decision (Hough 13/20).
  SAM 3 (run after access was granted): raw top-1 24/25, 19/20 at frame 0.
  Development estimate only; research code in `research/plate-detection/`.
  [Phase A.2](../analysis/PLATE_DETECTION_PHASE_A2_RESULTS.md), which detects once on frame 0 and then tracks with
  SAM 2.1 video, holds the plate on every labelled frame when the seed is right, but scores 276/388 frames overall
  (per-frame 320/388) because a wrong seed stays wrong.
  A [SAM 3 seed](../analysis/PLATE_DETECTION_SAM3_SEED_RESULTS.md) raises that to 24/25 clips on target on every
  labelled frame with SAM 2.1; EdgeTAM (phone-capable) matches it on 23/25 but re-locked onto a wrong object after a
  loss on one clip.

## Recommended plan structure

Plans should be executable by another engineer/agent without reconstructing the original reasoning.
Include, when applicable:

1. context and current state;
2. goal and explicit non-goals;
3. governing architecture/measurement constraints;
4. dependencies and prerequisites;
5. ordered work packages;
6. likely files/modules affected;
7. deterministic tests and validation commands;
8. evidence artifacts to retain;
9. decision/promotion gates;
10. stop conditions;
11. rollout/integration path;
12. documentation/issues/ADRs that must be updated if the work is promoted.

For research work, explicitly distinguish:

- **implement experiment**;
- **evaluate evidence**;
- **promote to production**.

Those are separate decisions. A successful experiment does not automatically justify production
complexity.
