# Automatic plate selection: design and development evaluation (#178)

Date: 2026-10-13. Work type: **audit/design**, then **evaluate evidence** only after
authorization. Parent: [#80](https://github.com/Szczepanov/openbar/issues/80);
coordination: [#110](https://github.com/Szczepanov/openbar/issues/110).

## Status and scope decision

**PROCEED — Prompt 1 only**, owner decision supplied in chat on 2026-10-15. This supersedes
the earlier DEFER only for the preparation below, not the future product specification.
The owner wrote:

> I authorize the bounded research work necessary to execute the Hough top-K target-recall experiment described in the existing automatic plate-selection research plan.
>
> This authorization applies ONLY to:
>
> 1. independent annotation/protocol preparation,
> 2. an observation-only Hough candidate instrumentation/harness,
> 3. experiment preregistration/freeze records,
> 4. verification of the resulting research tooling.

This committed plan is the durable record of that supplied decision; no #178 issue comment
or URL is asserted. The source attachment was `Wklejony tekst.txt` in this task, SHA-256
`0638e7eb7fe9a370e001c4f197f10486a1fabf3569b2a54ae16b6363792e216c`. The owner's
exact decision heading was `OWNER DECISION: PROCEED — Prompt 1 only`.

Research question: when the intended camera-facing outermost plate/end of the lifted barbell
is independently identifiable, where does it appear in the current Hough pipeline? The allowed
entry point is `research/vbt-workflow/hough_observe.py`, observing existing `suggest_plate`
without changing its code, defaults or `PlateParams`. Its CLI permits synthetic `--self-check`
only; the Python `observe_plate` API is for a future frozen executor. This preparation has
**zero real-media scoring runs**, no frame-path/decode CLI, one unchanged candidate, no models,
no added dependencies and no parameter search. Synthetic checks use the existing research
environment. Actual collection size, executor limits and scoring budget must be completed in
the private freeze before any real-data run; this decision supplies no numeric scoring budget.

Allowed roles are synthetic correctness, authorized exploration (never unseen evidence), and
eligible source/session-separated `split-frozen-eval` only after blind labels, repeatability and a
complete freeze permit access. Forbidden are `split-reserved-57`, validation-purpose clips,
formal #79 inputs and promotion of proposal-exposed exploration into unseen evidence. Stop on
role/hash mismatch, candidate exposure before independent labels, incomplete freeze or
repeatability, any parameter change, or attempted expansion beyond Prompt 1.

The [Prompt 1 preparation protocol](HOUGH_TOP_K_PREPARATION.md) defines the annotation record,
matching prerequisites, metrics, budget, precision analysis and readiness gates. The stage is
**READY-BUT-NOT-SCORED** tooling/protocol preparation; a complete data freeze and formal
scoring remain blocked by incomplete readiness verification and freeze/executor gates. The primary
checkout has recordings and annotation artifacts; the protocol's metadata inventory correction
requires auditing those before requesting additional capture. Prompt 2, learned rerankers, detectors, model
acquisition/training, production integration/default changes and M0/M1 architecture changes
remain unauthorized. This is no selection-accuracy or promotion decision.

For work beyond bounded Prompt 1, the owner must record a separate decision citing this plan's
revision, the allowed research entry point, input split, candidate configurations, resource
budget and stop criteria. A DEFER/STOP decision leaves #95/#113 unchanged. Even a research
PROCEED does not supersede [ADR-0008](../adr/0008-m0-go-no-go-and-m1-entry.md).
Production adoption needs the #80 M1 decision and relevant selection, recording-envelope,
physical-reference and #179 phone-runtime evidence. #79's methods/settings remain frozen.

Prompt 1 permits the separate observation harness and its tests, but changes no production code,
dependency, canonical schema/version, golden file, fixture or accepted ADR. The UI behavior
below is a future product specification, not an instruction to build UI.

## Existing implementation and evidence audit

| Existing part | Reuse | Limit / missing evidence |
| --- | --- | --- |
| #95 `research/vbt-workflow/vbt_suggest.py`: `suggest_plate`, `PlateParams`, `suggestion_id` | Existing `plate-hough-edge-v1` proposal, deterministic ranking/refinement, image-bounds checks and method/parameter identity | Hough circles on a downscaled seed frame, plausible radius fractions and radial edge support select a circle, not a semantically identified barbell plate. Highest support wins; no competing-target identity refusal. Fully contained circle required; obscured/cropped rims, small plates and background circles can defeat it. |
| #95 `session_contract.py`, `vbt_session.py` | Proposal preview, manual centre/radius edits, accepted/adjusted/manual provenance tied to the session | Every clip requires human confirmation. These statuses describe interaction, not independent accuracy. Ingest seed-frame choice does not establish selection before relevant movement. |
| #113 `session_machine_init.py`: `clip_reasons`, `clip_items`, `verify_record` | Fail-closed metadata/geometry validation, source/configuration hashes, machine-origin isolation and restart/idempotence checks | Confidence is range-checked, with no acceptance threshold. Plate **and stick** suggestions plus a known-diameter/exercise profile are required. This is not a plate-only import interface or an editable assumed-diameter profile. |
| #111 `assess_clip.py` and [assessment contract](../validation/VBT_CLIP_ASSESSMENT.md) | Separate processing, mechanical validity, experiment suitability and accuracy conclusions | Mechanical validity can coexist with wrong-object tracking; current suitability stays unknown/rejected and accuracy not established. No selection-accuracy promotion switch. |
| #112 [rules](../analysis/VBT_SUGGESTION_EVALUATION_RULES.md), [results](../analysis/VBT_SUGGESTION_EVALUATION_RESULTS.md), #113 [decision](../analysis/VBT_INITIALIZATION_DECISION.md) | Retained development geometry comparisons and documented evidence limits | Eight clips from one session; all 32 items adjusted after seeing proposals. Plate-centre mean discrepancy 2.02 px, maximum 3.85 px; not blind identity truth. Target-identity mistakes and explicit rejections cannot be derived; no retained #111 assessments for these clips. |

The #113 owner decision remains limited to its opt-in research path. Machine-origin records
remain research-only, never human-confirmed or consumer-eligible. Its initialization writes no
seed/CSV; `run-research` is a separate research consumer. See the
[existing contract](../validation/VBT_RESEARCH_INITIALIZATION.md).

Relevant existing tests: `test_vbt_suggest.py` (geometry, clutter, determinism/failures),
`test_vbt_session.py` (confirmation/status behavior), `test_session_machine_init.py`
(profile/refusal/provenance/isolation/restart), and `test_session_machine_run.py`
(research execution and non-promotion), under `research/vbt-workflow/tests/`.
Passing them establishes implementation behavior, not selection accuracy on imported videos.

## Candidate approaches and recommendation

1. **Evaluate the existing circle proposer first.** Retain its exact parameters and output,
   including failures; add no model. This gives the least costly baseline and exposes the
   identity/refusal gap. Do not claim its current highest-score rule resolves ambiguity.
2. **Bounded temporal candidate comparison**, only in a separately approved follow-up: reuse
   the proposer on a fixed timestamp schedule and evaluate identity consistency. More decode
   work; consistent background circles can still win. Freeze frame search, tie/ambiguity and
   refusal rules before scoring; no silent switching to another plate.
3. **New detector/model** only if reviewed failures justify it and the owner approves source,
   licence, distribution, resource cost and acquisition. No model is acquired by this plan.

Recommend option 1 as the development baseline, plus the current human-confirmed path as an
interaction comparator. Do not clone #95 or change its defaults. The baseline may fail the
future behavior below; that is a valid evaluation outcome, not a reason to hide failures.

## Future import and correction behavior

Imported approximately side-on, fixed-camera videos are the initial intended geometry.
Side/fixed footage remains warning-only under current support policy; selection does not
upgrade it to supported. Unsupported views/camera motion, invalid geometry or unavailable
authoritative timestamps produce an explicit refusal, with no fabricated measurement result.

| Situation | Required behavior |
| --- | --- |
| One identifiable intended plate before movement | Show the selected circle on the decoded, display-oriented seed frame, its timestamp and editable diameter. No mandatory tap to start the future automatic path; preview/correction remains accessible before and after results. |
| Diameter | Start at **0.45 m**, clearly labelled an assumption. Permit a finite positive user value for the actual selected plate, including smaller plates. Pixel radius does not prove physical diameter. An edit invalidates dependent calibration/metrics and requires recomputation retaining the prior selection. |
| Manual correction | Allow choosing a different plate and editing centre/radius on a chosen frame. Keep original proposal and correction separately. Refusal exposes manual selection explicitly; never silently converts to success. Invalid/out-of-frame/non-finite edits are refused. |
| Multiple plates / background circles | Intended target is the visible outer rim of the outermost plate on the camera-facing end of the lifted barbell. If this cannot be uniquely identified, refuse automatic selection and ask for correction; do not pick the largest circle or highest tracker confidence as an identity rule. User selection of another rim also requires that rim's physical diameter. |
| Occlusion, blur, no target, cropped rim | Refuse when centre/radius or target identity cannot be established under the frozen candidate rules; state the reason. A human may choose a usable earlier frame; no invented circle or certainty from missing evidence. |
| Target appears late / clip starts mid-lift | Automatic seed must precede the first relevant movement intended for analysis, using PTS. If no usable earlier frame exists, refuse full-lift analysis. Any future partial analysis needs an explicit user-selected interval and reports missing movement; no backfilled path. |
| Timing and results | Read nominal FPS for display only; use frame PTS for search/seed/analysis, including VFR. Display selection state separately from downstream tracking loss/quality. No trajectory across loss and no identity switch during tracking. |

Selection before movement is an evaluation requirement, not an exercise-recognition feature.
The prototype's search horizon/schedule must be bounded and frozen; independent movement-onset
labels are available only to scoring, never as candidate inputs. No whole-body pose, exercise
recognition, cloud, live camera or new stick requirement is implied for the future product.

## Proposed provenance boundary

Keep the automatic proposal, refusal reasons and final selection outside canonical Analysis
until a separately reviewed contract exists. Design terms here are not new serialized enums:

- `automatic`: unchanged machine geometry, with no implied human confirmation;
- `manual`: human geometry where no machine proposal was used;
- `corrected`: original machine proposal plus final human geometry/target/frame changes.

Record source hash, display dimensions/orientation, seed frame index **and authoritative
timestamp_s**, proposed/final centre/radius in px, method/version/configuration, code/environment
identity and any model hash/licence. Retain diameter value in metres and its source
(default assumption, user-supplied assumption, or documented independently measured value).
Store per-component edits so diameter-only correction cannot masquerade as geometry correction.
Preserve current #95 accepted/adjusted/manual and #113 machine contracts; never relabel old data.

Selection scores retain algorithm-specific meaning; neither edge support nor tracker confidence
is a selection-accuracy probability. Calibrated positions/kinematics remain in `openbar-core`.
Any eventual adapter proposes existing manual-seed geometry, validates it under ADR-0007,
and preserves raw observations, explicit loss, finite values and deterministic provenance.
Timing measurements live in separate evidence artifacts, not deterministic measurement output.

## Development input roles and independent truth

No new retained videos or independent labels are supplied by this docs change. The following
is the required collection matrix, not a claim that challenge evidence already exists.
Use owner-authorized development inputs only; retain private media privately and record rights.
Exclude formal #79 inputs and #57 held-out inputs from collection, inspection and tuning here.

| Required challenge examples | Independent annotation / expected policy |
| --- | --- |
| Clean side-on plate before lift | Unique identity, centre/radius and movement-onset PTS; selectable |
| Several plates on bar, another loaded bar, background circles | Distinct identities and visible outer rims; unique target or ambiguity refusal |
| Partially obscured / cropped rim, motion blur, low contrast | Visibility and geometry uncertainty; select only when independently resolvable |
| Smaller-diameter plate and mixed sizes | Target identity, px geometry and independently recorded diameter when available; default remains an assumption |
| Target appears after onset / no pre-movement frame | First usable visibility and movement onset; full-lift refusal |
| No plate / no lift target | Negative identity label; no-target refusal |
| Oblique/front view or moving camera | Unsupported-input label; refusal even if a circle is found |
| Rotated display, VFR / irregular PTS | Display-coordinate geometry and PTS labels; no nominal-FPS timing substitution |

Before any scoring, create a hashed manifest of all planned clips with immutable role assignments:
**development/exploration** (including the eight already-seen #112 clips, if authorized),
**frozen development evaluation** (unseen independent recording sessions), and
**synthetic correctness**. Split by recording session/source recording, including derived crops,
so adjacent clips cannot cross roles. Synthetic results are reported separately and do not
count as real accuracy evidence. If independent sessions are unavailable, report insufficient
evidence rather than treating the same session as an evaluation split.

Two annotators label target identity, acceptable/refusal state, seed-frame circle and relevant
movement onset without seeing candidate overlays/scores or #95 edits. Reconcile disagreements
before unblinding; retain both labels, adjudication, centre/radius repeatability and onset
uncertainty. An unresolved identity/geometry/onset remains indeterminate. Model agreement,
tracker consensus, WL output and a user accepting a proposal are not ground truth.

## Freeze and scoring protocol

After scope approval, retain a freeze record **before the first scored run**, binding this plan,
manifest/labels, candidate code/configuration and source hashes. Declare frame-search horizon,
timestamp sampling, target/tie/refusal rules, time/resource limits, measurement environment and
repeat count. Baseline uses unchanged `PlateParams`; it has no new semantic ambiguity check.
If a new rule is added, it is a separately named candidate frozen before evaluation.

Do not derive an acceptance threshold from the scored split. Record any planned numeric
geometry/latency decision limits and their rationale before scoring, or declare the evaluation
descriptive with **no pass/promotion verdict**. This plan chooses the latter for the initial
baseline; #179 separately owns the numeric Android workload/budget. Revising a candidate after
unblinding makes that split development-only and requires a fresh frozen evaluation split.

Count each import once for selection outcomes, before any manual correction:

| Outcome | Definition |
| --- | --- |
| Accepted-correct | Candidate emitted a target; independently labelled unique intended target matches, is on a usable pre-movement frame and input is eligible. Geometry error is reported separately. |
| Wrong-target | Candidate emitted a different object/rim than the independently unique intended target. A geometrically close different object remains wrong. |
| Unsafe acceptance | Emitted selection on an independently required-refusal case (ambiguity, no-target, unsupported input, or no usable pre-movement seed). |
| Missed/refused | No selection within the frozen budget on an independently selectable clip. Retain reason and separate timeout/error from deliberate refusal. |
| Correct refusal | No selection on an independently required-refusal clip; retain reason. |
| Timing failure | Correct identity on eligible input, but seed is not before relevant movement; no full-lift success. |
| Indeterminate | Reference cannot resolve identity/eligibility/onset; report emitted/refused state without awarding correctness. |

For unique eligible inputs, wrong identity takes precedence over timing failure; ambiguous,
unsupported, no-target and no-pre-movement cases belong to unsafe acceptance/correct refusal.
Outcomes are mutually exclusive, and counts reconcile to every manifest row, including errors.

Report counts, denominators and 95% Wilson intervals for accepted-correct/eligible,
missed-refused/eligible, wrong-target/eligible, wrong-target/emitted unique-eligible selections,
unsafe-acceptance/required-refusal, and correct-refusal/required-refusal. Show emitted counts,
timing failures, indeterminate truth and timeouts/errors explicitly. Empty denominators are
not estimable, never zero error. Report per challenge stratum and recording session alongside
pooled totals; clips from one session are correlated, so pooled intervals do not establish
population performance. If intervals are narrow only through repeated frames, do not claim
independent evidence: frame observations are not additional import trials.

On accepted-correct clips with usable independent geometry, report centre Euclidean error in px,
radius signed/absolute error in px and signed relative error `100 * (proposal - reference) /
reference`, requiring a finite positive reference radius. Give n, missing-reference counts,
median, mean, p95, maximum and annotation uncertainty. Also retain per-clip geometry errors for
wrong targets where labels permit; never exclude them to imply overall low error. Use the
actual emitted precision without half-pixel shifts (ADR-0007). Diameter uncertainty and physical
scale accuracy are separate; seed geometry accuracy does not validate tracking/kinematics.

Retain cold and warm wall-clock selection latency separately, from import request through
probe/decode/search to preview/refusal; record each stage, hardware, OS/runtime, input codec,
duration/resolution, decoded frame count, timeouts and peak memory if available. Freeze cold/warm
definitions and repeat count; repeated timings are not extra accuracy samples. Report n, median,
p95 and maximum including failed-import durations. Desktop diagnostics do not satisfy Android
or end-to-end results-within-seconds claims. Preserve faster-than-video-duration ADR-0009 and
#179's separately predeclared product target; do not invent a phone budget from this baseline.

## Execution, retained evidence and review gates

1. **Now:** execute only the bounded Prompt 1 preparation authorized above; use its readiness
   gates. The wider prototype and all later prompts remain deferred.
2. **After PROCEED:** retain input rights/roles and independent labels; freeze the candidate,
   bounds and descriptive/no-promotion status before scoring. Refuse incomplete freeze records.
3. **Implement only the approved research harness:** reuse proposer/provenance checks; do not
   mutate #95/#113 records, add consumer writes, select production defaults or alter assessments.
   Version any new persisted research format and review schema/docs before implementation.
4. **Validate:** run existing suggestion/initialization tests and targeted tests for ambiguity,
   negative/unsupported/late-visible inputs, invalid diameter/geometry/PTS, deterministic ties,
   timeout/refusal, correction retention and restart/non-promotion. Check every manifest row
   has one outcome and hand-check denominators/geometry on small known cases.
5. **Evaluate and retain:** exact commands and environment, frozen hashes, raw proposals and
   refusals, blind/adjudicated labels, per-clip scores and latency trials, aggregate tables,
   missing evidence and all failures. No success-only gallery or adjustment-derived truth.
6. **Review outcome:** STOP, DEFER for missing evidence, or propose a separately scoped next
   research step. Any adoption decision remains with #80; #176 tracker completion and #57
   candidate selection do not establish initialization accuracy.

Stop on excluded study/held-out inputs, unreviewed model/dependency acquisition, lost provenance,
truth leakage, canonical contract changes or attempts to present machine origin as confirmation.
Reopen the relevant scope/contract decision instead of silently widening this plan.

## Completion boundary for this preparation

This document supplies the #95/#113 audit, future UX/failure spec, development test roles,
independent-truth protocol, scoring/uncertainty definitions and bounded **Prompt 1 PROCEED**.
Verification of existing input eligibility, independence and challenge coverage remains pending;
additional collection is needed only for demonstrated gaps. A complete frozen run record,
measured quality/latency and wider prototype authorization also remain pending; no accuracy or
M1 claim follows from a docs PR. No proprietary implementation/assets/models are used; owner-observed product
behavior informs requirements only.
