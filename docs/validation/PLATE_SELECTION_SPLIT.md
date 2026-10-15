# Plate-selection evaluation: clip roles and collection checklist

Parent plan: [AUTOMATIC_PLATE_SELECTION_PLAN.md](../plans/AUTOMATIC_PLATE_SELECTION_PLAN.md) (#178).
Date: 2026-10-14. Work type: **evidence preparation**. This document assigns roles to existing
private clips and lists the collection and labelling the plan requires. It authorizes no
detector, threshold or consumer behaviour. The owner has now supplied **PROCEED — Prompt 1
only**, durably recorded in the parent plan. The [Prompt 1 protocol](../plans/HOUGH_TOP_K_PREPARATION.md)
supplements this collection checklist; no formal media is scored by that preparation.

## Goal

Reduce human work in plate seeding by letting agents propose the seed, with the human moving
from seeding every clip to auditing a sample. That needs independent evidence of how often an
automatic proposal is right. Existing labels cannot supply it: they were made on one
operator's footage, mostly after seeing proposals, and the held-out clips are reserved.

## Roles

Roles are immutable once a freeze record exists. They are recorded as `challenge_tags` in the
private manifests (`validation/private/manifest.json`, `validation/private/vbt/manifest.json`);
no schema change is needed. Tag meanings:

| Tag | Role | Who may look at the clip | Count today |
| --- | --- | --- | ---: |
| `split-exploration` | Development / exploration: agents may inspect, tune and debug against these | agents and owner | 13 |
| `split-reserved-57` | Reserved #57 held-out tracker/filter evidence. **Excluded** from plate-selection collection, inspection and tuning ([plan](../plans/AUTOMATIC_PLATE_SELECTION_PLAN.md#development-input-roles-and-independent-truth)) | nobody, for this work | 6 |
| `split-frozen-eval` | Frozen development evaluation: unseen independent recording sessions, scored once per frozen candidate | owner labels; agents see only after the freeze record | 0 (to collect) |

Current assignments:

| Clip | Manifest | Existing purpose | Tag |
| --- | --- | --- | --- |
| self-snatch-ohs-side-001 | `private/manifest.json` | development | `split-exploration` |
| self-hang-snatch-side-002 | `private/manifest.json` | development | `split-exploration` |
| self-back-squat-side-002 | `private/manifest.json` | development | `split-exploration` |
| self-clean-jerk-side-002 | `private/manifest.json` | development | `split-exploration` |
| self-snatch-side-002 | `private/manifest.json` | development | `split-exploration` |
| vbt-* (8 clips, one session) | `private/vbt/manifest.json` | development | `split-exploration` |
| self-back-squat-side-001 | `private/manifest.json` | validation | `split-reserved-57` |
| self-clean-jerk-side-001 | `private/manifest.json` | validation | `split-reserved-57` |
| self-hang-snatch-side-003 | `private/manifest.json` | validation | `split-reserved-57` |
| self-clean-pull-side-003 | `private/manifest.json` | validation | `split-reserved-57` |
| self-clean-side-004 | `private/manifest.json` | validation | `split-reserved-57` |
| self-clean-side-005 | `private/manifest.json` | validation | `split-reserved-57` |

## What the existing clips can and cannot show

Seed ground truth (centre, radius, timestamp at one frame) exists for 18 clips: 10 `self-*`
seeds plus the 8 `vbt-*` seeds. `self-snatch-ohs-side-001` has no seed. Seeds were taken from
the owner's first labelled frame, and the `vbt-*` geometry was adjusted after seeing proposals,
so none of this is blind identity truth (see the plan's audit table).

All exploration clips are fixed side view, good lighting, no occlusion, 450 mm plates, one
lifter and one barn. They support debugging and baseline behaviour on easy footage. They do
**not** support a claim about unfamiliar gyms, ambiguity or refusal.

## Required rules

- Split by recording session, not by clip. Clips from one session never cross roles. This is checked by
  `python validation/tools/manifest_roles.py --manifest validation/private/manifest.json` (session = `session-YYYYMMDD`
  tag, else the `VID_YYYYMMDD_` file-name date; exit 1 on a breach). The 2026-10-02 session is a known breach
  (development and reserved clips share that day); it can only be passed explicitly with `--accept session-20261002`.
- A `split-exploration` clip may not become `split-frozen-eval`, and no role changes after
  the freeze record.
- No candidate code, threshold or parameter is tuned on `split-frozen-eval` clips. A candidate
  revised after unblinding makes that split development-only and needs a new frozen split.
- `split-reserved-57` clips are not opened, decoded, annotated or scored by this work.
- Labels come before any candidate output is seen. Model agreement, tracker consensus and
  a human accepting a proposal are not truth.
- The initial baseline is **descriptive**: counts, denominators and Wilson intervals, with no
  pass/promotion verdict (plan, "Freeze and scoring protocol"). Any autonomy gate, audit
  sampling rate or auto-accept threshold is a later, separately approved decision made from
  this evidence. Nothing here proposes one.

## Collection target for `split-frozen-eval`

The plan's required challenge matrix is the target. The numbers below are historical collection
suggestions, not a statistical minimum or an approved Prompt 1 sample size. Use the Prompt 1
precision analysis before collection; report insufficient evidence when independent sessions
are unavailable.

- At least **3 recording sessions**, each on a different day, and at least 2 different
  places or lighting setups.
- About **3–5 clips per session**, roughly 12–15 in total.
- Coverage across the sessions, each row at least once:

| Challenge | Expected policy | Suggested `purpose` |
| --- | --- | --- |
| Clean side-on plate before lift | selectable | development |
| Several plates on the bar, another bar or background circles in frame | select outer rim, or refuse if ambiguous | development |
| Bystander walking through frame | selectable or refuse | development |
| Partly occluded or cropped rim (rack upright, legs, frame edge) | select only if resolvable | development |
| Smaller plate or mixed plate sizes (record the real diameter) | selectable, diameter edited | development |
| Plate appears after the lift has started | full-lift refusal | boundary |
| No plate / no lift target | no-target refusal | boundary |
| Oblique, front, or moving camera | unsupported refusal | ood |
| Rotated display and variable frame rate | selectable | development |

Do not duplicate any video already hashed in the manifests (the novelty exclusion set in
[VBT_AGREEMENT_STUDY_STATUS.md](../analysis/VBT_AGREEMENT_STUDY_STATUS.md)).

## Checklist for the owner

The earlier checklist follows. For Prompt 1, the linked protocol supersedes the interim label
fields, repeatability and freeze sequencing below. PROCEED is recorded; data readiness is not.

1. **Confirm the roles.** Read the tables above. If you disagree with any assignment, say so
   before step 2; the tags are applied by the manifest edit in this change.
2. **Confirm the collection target** (sessions, clips, matrix). Change the numbers above if
   they are wrong for your situation.
3. **Film the sessions.** Keep the original files untouched, one file per attempt, camera
   fixed except in the oblique and moving-camera clips. Note place, date, plate diameter and
   whether anyone else was in frame.
4. **Copy to private storage and check novelty.** Put files under `validation/private/media/`.
   Compute each SHA-256 and confirm it appears in no existing manifest:

   ```bash
   sha256sum validation/private/media/<new-file>.mp4
   grep -rl "<that-hash>" validation/private/manifest.json validation/private/vbt/manifest.json
   ```

   No output from `grep` means the hash is new. Never commit the video.
5. **Add manifest entries.** Copy a similar existing fixture entry in
   `validation/private/manifest.json`, set a fresh `id`, the hash, the purpose from the table
   and the real conditions. Add the tags `split-frozen-eval` and the challenge name. Run:

   ```bash
   python validation/tools/schema_check.py
   ```

6. **Label blind.** Do not use `vbt_session.py` for this: it pre-fills a machine suggestion,
   which leaks the candidate into the label. Use the plain frame labeller
   (`label_package.py`, see [MANUAL_TARGET_SEED.md](MANUAL_TARGET_SEED.md)). Keep the output
   outside the production seed directory, under `validation/private/frozen-eval/`.

   For each clip that should be *selected*, create a package at a time before the first
   movement, click the plate, and convert the downloaded CSV:

   ```bash
   python validation/tools/label_package.py --manifest validation/private/manifest.json --fixture <id> --at-s <seconds> --annotator-id <a-or-b> --output-dir validation/private/frozen-eval/work/<id>.<a-or-b>
   # open index.html: click centre, Shift+click rim, press 1/2/3 for quality, download CSV
   python validation/tools/annotations.py seed --manifest validation/private/manifest.json --metadata validation/private/frozen-eval/work/<id>.<a-or-b>/metadata.json --csv <downloaded.csv> --output validation/private/frozen-eval/seeds/<id>.<a-or-b>.manual-target-seed-v1.json
   ```

   For every clip, including refusal cases, add one row per annotator to
   `validation/private/frozen-eval/labels.csv` (interim format; the freeze record will pin it):
   `fixture_id,annotator,expected_policy,movement_onset_s,target_description,diameter_m,notes`,
   where `expected_policy` is `select`, `refuse_ambiguous`, `refuse_no_target`,
   `refuse_unsupported` or `refuse_no_pre_movement`. Refusal clips have no seed file.

   Formal Prompt 1 cases require two independent annotators who do not see candidates, scores,
   earlier results or each other's labels. A delayed second pass by the same person estimates
   intra-annotator repeatability only; it does not replace the independent passes. Keep both
   labels and blind adjudication, including unresolved cases; do not overwrite originals.
7. **Bind the recorded owner decision.** Cite the committed parent plan's Prompt 1 decision;
   do not invent a #178 comment. Wider work requires its own owner decision.
8. **Complete the readiness gates.** Retain the freeze record
   ([template](PLATE_SELECTION_FREEZE_RECORD.md); hashed manifest,
   labels, candidate code and configuration, frame-search horizon, refusal rules, repeat
   count) before the first scored run. The current preparation builds and checks only the
   observation harness on synthetic inputs. It does not score `split-frozen-eval`.

If steps 3–6 are not completed, the correct report is "insufficient independent evidence",
not a result on the exploration clips.

## Not changed by this document

No code, schema, version constant, golden file, fixture or ADR changes. The role tags live
only in the git-ignored private manifests. This document is the committed record of the split.
