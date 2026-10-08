# Research initialization proceed/stop decision (#113)

2026-10-08. Owner decision required by #110 and the first acceptance criterion of #113 before any
machine-initialized research session work starts.

**Decision: proceed, bounded to the #113 scope below.** Recorded by the owner on 2026-10-08.

## Process deviation

#113 asked for proceed criteria to be recorded before #112 aggregation. That did not happen: the
[#112 results](VBT_SUGGESTION_EVALUATION_RESULTS.md) were calculated under
[frozen evaluation rules](VBT_SUGGESTION_EVALUATION_RULES.md) that deliberately declared no
proceed criteria. This decision is therefore made after the numbers were seen. It must not be
cited as a predeclared test, and no threshold below is derived from or tuned to those numbers.

## Evidence cited

From #111 ([assessment contract](../validation/VBT_CLIP_ASSESSMENT.md)):

- Processing, mechanical validity, experiment suitability and independent accuracy are separate
  conclusions. The first slice can only reject mechanically or leave suitability unknown;
  accuracy is never established by it.
- None of the eight #112 clips has a retained assessment. All four conclusions are unknown or
  not established for every clip.

From #112 (eight retained owner-confirmed development clips, one session):

- 32/32 items were adjusted; accepted, manual, skipped and missing-proposal counts are all zero.
- Proposals were close to the confirmed edits: plate-centre distance mean 2.02 px (max 3.85 px),
  radius signed relative median +0.63%, low/high marker median 4.67/5.52 px, stick-length signed
  relative median -0.25%.
- Target-identity mistakes and explicit rejections are not derivable from #95 records.
- Edits were made after seeing proposals (possible confirmation bias); not blind, not held-out.

## Limits that bind either outcome

- The evidence covers seed-frame geometry only. It says nothing about tracking, kinematics,
  recording support or physical accuracy.
- An absent target-identity error rate is unknown, not zero. One session cannot estimate how often
  the suggester picks the wrong object on other videos.
- No automatic-acceptance threshold, calibrated confidence or production default follows.
- #57 held-out data stays out of development; #79, #53/#58/#59 and #57 remain separate tracks.

## What each outcome authorizes

**Proceed** authorizes only the #113 scope: an opt-in, separately named research mode beside the
unchanged #95 confirmation path, with a validated profile (known plate diameter and exercise),
explicit machine-origin status and provenance, fail-closed rejection of ambiguity or missing
metadata, and restart/idempotence proof. Machine-origin sessions stay research-only, cannot
acquire accepted/adjusted/manual or human-confirmed status, and are ineligible for consumer
live-trial writes. It does not authorize unattended use, canonical Analysis changes, tracker or
default selection, or tuning.

**Stop** keeps #113 blocked. Human confirmation remains the only initialization path. Revisit
only with new evidence that addresses the gaps above, such as retained #111 assessments, explicit
target-identity labels, or clips beyond this one session, with proceed criteria recorded before
that evidence is aggregated.

## Rationale

The #112 proposals were close to the owner's edits on every retained clip, so a machine-initialized
research path is worth building and measuring. The risk is contained by construction rather than by
the evidence: the mode is opt-in, its records can never pass as human-confirmed, it produces no
consumer writes, and the #95 confirmation path stays available as the fallback for every clip.
Proceeding does not claim the suggester is accurate; machine-initialized sessions are themselves
research evidence to be assessed, not measurements to be trusted.
