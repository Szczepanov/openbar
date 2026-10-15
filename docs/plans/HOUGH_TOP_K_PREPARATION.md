# Hough top-K: Prompt 1 preparation and preregistration protocol

Date: 2026-10-15. Authority: the owner's **PROCEED — Prompt 1 only** recorded verbatim in
[the parent plan](AUTOMATIC_PLATE_SELECTION_PLAN.md#status-and-scope-decision).
Stage: **READY-BUT-NOT-SCORED** tooling/protocol preparation. This is neither a completed data
freeze nor permission to score incomplete inputs. No real media is processed by this preparation.

Question: when the intended camera-facing outermost plate/end of the lifted barbell is
independently identifiable, where does that target appear in the current Hough pipeline?
The answer must separate raw proposals, truncation, fitting, ranking and refinement.

## Starting evidence and boundary

The retained evidence audit is `docs/analysis/HOUGH_TOP_K_EVIDENCE_AUDIT.md` in PR #198,
commit `4b7dd0621473b8d995e8b03065caaeaa3f87a067`; its audited base was
`c64011636e9312e01f810b243b84fedc99cc4a3d`. The preparation started from that unchanged
main base. The audit is a historical insufficient-evidence finding, not a new accuracy result.
Its missing authorization is now supplied. Its file-availability observation was limited to
the isolated worktree; the primary checkout has recordings and annotation artifacts, as below.

### Primary-checkout inventory correction (2026-10-15)

A metadata-only check of the primary checkout found 54 entries in
`validation/private/manifest.json`: six exploration, six reserved-57 and 42 frozen-evaluation
candidates. The separate VBT manifest adds eight exploration clips, for 14 exploration entries
across the two manifests. These are manifest counts, not verified unique/independent recordings
or scored cases. The manifest SHA-256 values at inspection were respectively
`b3fb03cd8600497e5da59bbb579c375c3c8774130d98bc893fc19390ca59f0ef` and
`1ff1e62554f31e760104415877474423be7bfafd3a9fe51c43097b8ca277a315`.

Of the 42 frozen candidates, 13 have `purpose: validation` and remain excluded by the existing
guard, despite their split tag. The other 29 (17 ood, 12 boundary) are only potentially eligible:
rights, source/session independence, #79 exclusions, prior exposure and label provenance still
need checking. No roles were changed. Private frozen-evaluation annotation, label and seed
directories exist; their contents were not inspected. No completed freeze directory was found.

The private `AGENT_PREVIEW_DISCLOSURES.md` records prior owner-authorized scene-classification
previews, and `REPORT_LIMITATIONS.md` records shared-session, assumed-diameter and unverified
metadata limits. These disclosures must enter any eventual freeze; a split tag alone does not
establish unseen evidence. This check read manifests, directory inventories and those disclosure
notes only: no footage, target coordinates, candidate outputs or evaluation-label contents.

Audit existing recordings and label provenance before requesting more footage. Collect additional
inputs only to fill demonstrated eligibility, independence, annotation or challenge-coverage gaps.

The [retained eight-clip evaluation](../analysis/VBT_SUGGESTION_EVALUATION_RESULTS.md) used
proposal-exposed edits from one session. Its 2.02 px mean centre discrepancy and radius edits
cannot set independent matching tolerances or establish identity. The
[radial failure analysis](../analysis/M0_RADIAL_GEOMETRY_FAILURE_ANALYSIS.md) and
[mask-guided experiment](../analysis/MASK_GUIDED_RIM_EXPERIMENT_RESULTS.md) do not supply
independent outer-rim truth. Prepared repeat-annotation pages are not completed annotations;
centre repeatability alone cannot establish radius/rim repeatability. The
[plate-scale study](../analysis/PLATE_SCALE_STUDY_RESULTS.md) does not validate physical scale.
The [colour/hub result](../analysis/COLOUR_HUB_CUE_RESULTS.md) reports centre-label noise about
1.4–3.1 px and unresolved small effects, not independent outer-radius or identity repeatability.
No historical SAM, tracker, confidence, accepted suggestion or adjustment becomes truth here.

Preserve [split roles](../validation/PLATE_SELECTION_SPLIT.md), #57/#79 freezes and ADR-0008.
Synthetic correctness is separate from real evidence. Authorized exploration remains exposed;
never relabel it unseen. Frozen evaluation requires source/session-separated recordings with
verified provenance, rights, immutable hashes and novelty checks including derived crops; existing
eligible recordings may satisfy this without new capture. No reserved-57 or
validation-purpose clip may be opened, decoded, annotated or scored by this work. No Prompt 2,
reranker, detector, model/training/acquisition, dependency, production integration/default change,
canonical output or M0/M1 promotion is authorized.

## Observation surface and fixed budget

`research/vbt-workflow/hough_observe.py` calls existing `vbt_suggest.suggest_plate` using the
unchanged default `PlateParams`. A Python profile hook captures actual function locals on
return and the `_refine` return tuple, retaining the algorithm's result separately. It does
not replace Hough, scoring, sorting or refinement. The hook must be restored on success and
exception. It is a synchronous research observation surface, not a production profiler.
Its research harness identity is `hough-stage-observer-v1`; bind that ID and its source hash.

The record must retain raw Hough count/order/index and small-image geometry; image dimensions,
scale and display conversion; first-60 retention; fit flags for all raw circles; the exact
ranked survivors and unrounded support/geometry with raw index; selected pre-refinement circle;
refinement tuple/support; and the unchanged final suggested/failed dictionary. Fit flags for
truncated circles are diagnostic applications of unchanged `_fits`, not stages actually executed
by the baseline. Duplicate geometries retain distinct raw indices and stable order.

Current semantics: Hough max side 960; radius fractions 0.04–0.30; `dp=1`, Hough thresholds
120/25; minimum distance `max(4, int(0.05 * shorter * scale))`; radius bounds
`max(1, int(0.04 * shorter * scale))` and `max(2, int(0.30 * shorter * scale))`.
Centre conversion is `(value + 0.5) / scale - 0.5`, radius is `value / scale`.
Only first 60 raw proposals reach fitting, then support ranking descending with ascending
x/y/radius ties. Canny is 40/120; rank tolerance 2 px; refinement tolerance 1.5 px; alignment
0.7; 360 rim samples; minimum refined support 0.4. Refine steps/spans are `(2,3), (1,2),
(0.5,2)`; only top-1 is refined, with no fallback. Rounding/bounds refusal stays separate.
Freeze the full parameter dump and source hashes, not merely this summary.

Preparation budget: one unchanged candidate, zero parameter sweeps, zero real-media runs,
zero new models/dependencies, existing local research environment, bounded synthetic tests only.
The only CLI execution surface is:

```powershell
python research/vbt-workflow/hough_observe.py --self-check
```

There is no frame/media-path processing CLI, formal scorer or frozen executor in this slice.
The Python `observe_plate(image)` API accepts an already decoded display-oriented BGR array;
it cannot know the array's source role/hash or enforce annotation independence. A future approved
executor must validate the complete freeze, role guard, source/frame/label hashes and limits
before decoding or calling it. Do not invoke the API on real inputs to bypass those gates.
No run-ready claim follows from a passing synthetic check. Instrumentation costs are not
baseline runtime evidence; no phone/latency result is inferred.

## Independent annotation workflow and research record

These are required research-record fields, not a canonical JSON schema or a change to existing
seed formats. Store private records under `validation/private/frozen-eval/`; pin their encoding,
field definitions, revision and hashes before collection. Missing and not applicable are explicit
null plus reason; no fabricated zero geometry. Validate finite coordinates/uncertainty, positive
radius, dimensions, PTS/timebase and source/frame binding. Preserve original pass files unchanged.

| Group | Required fields and meaning |
| --- | --- |
| Identity/provenance | Record revision, case ID, source ID, immutable media SHA-256, source-recording and session IDs, role/purpose, rights reference, annotator ID, pass ID, actual annotation time, tool/version and exposure declaration |
| Frame | Frame identity/index plus authoritative PTS and timebase/seconds, decoded frame hash, width/height, display orientation/rotation and transform; integer pixel centres, +X right/+Y down |
| Timing/context | Lift type or unknown, relevant movement-onset PTS and uncertainty interval/reason, frame-before-onset status, frame-choice rule; unavailable onset stays unknown |
| Policy | Target visible, target selectable, required refusal, reason(s), supported/unsupported/indeterminate input; explicit no-target, ambiguity, no-pre-movement and geometry-unresolvable reasons |
| Target identity | Intended lifted barbell and camera-facing end ID/description, outermost visible plate and outer-rim ID, independent scene/object inventory and identity evidence; no largest-circle shortcut |
| Geometry | Centre x/y px, outer radius px, per-pass centre uncertainty bound and radius uncertainty bound px with rationale, rim arc/landmark evidence; null when unresolved |
| Challenges | Concentric-rim ambiguity and identifiable competing rim radii/IDs; crop proximity/minimum rim-to-border distance px; occlusion and visible arcs; blur; contrast/dark plate; multiple plausible plates/barbells; circular distractor inventory/IDs/classes; unknown allowed |
| Reconciliation | Both immutable pass references/hashes, agreed/adjudicated/unresolved status, adjudicator/evidence, final identity/policy/geometry and uncertainty, discrepancy values, exclusions and reasons |

Choose one pre-movement decision frame per clip by a fixed, label-independent acquisition rule
recorded before candidate execution (for example a fixed acquisition PTS); do not search frames
using Hough or onset labels. If that frame is not usable/pre-movement, retain the refusal or
indeterminate case. The scorer may inspect independently labelled onset; the candidate may not.
No frame scheduling optimization is included. Freeze exact requested PTS, decoded-frame selection
tie rule and actual PTS before unblinding. A change requires a new record.

Two independent annotators label every formal case on plain frames without candidate circles,
ranks, scores, existing results, seeds or each other's labels. Full scene context for lifted-end
identity/onset may be shown under a fixed protocol, without algorithm output. Repeated passes by
one person estimate intra-annotator repeatability only and do not silently replace independent
annotators. Record exposure violations and quarantine the case before outputs are released.
Reconcile only after both passes are saved/hashed, still without candidate outputs. Preserve
agreed, adjudicated and unresolved cases; disagreement never becomes a forced label. Geometry
from the plain frame labeller may supplement the record, but its centre/radius CSV alone lacks
identity, policy and uncertainty fields above. Do not use the suggestion-prefilled session page.

## Repeatability and candidate membership: before candidate visibility

First collect a separate, blind repeatability pilot with the same display/annotation procedure,
source-separated from formal evaluation and with no algorithm exposure. Two independent passes
must identify the same outer rim; a second independent round estimates repeat placement. Include
the planned resolution/radius and challenge ranges. Retain disagreements and unresolvable cases,
not only clean agreement. Report n, session counts, centre Euclidean disagreement and absolute
radius disagreement, distributions and uncertainty. Shared bias remains unmeasured.

Deterministic proposed tolerance derivation, frozen now before that pilot: for independently
resolved same-rim paired pilot annotations, let `Tc` be the nearest-rank 95th percentile
(`sorted[ceil(0.95*n)-1]`) of centre disagreement px, and `Tr` the same quantile of absolute
radius disagreement px. No multiplier, candidate residual, scale fitting or post-unblinding
adjustment is allowed. Freeze numeric Tc/Tr, pilot hashes and justified pilot coverage/precision
before formal candidate visibility. If the pilot is empty, has inadequate coverage/precision,
has zero tolerances inconsistent with tool resolution, or cannot distinguish the intended outer
rim from independently labelled concentric alternatives, matching is **NOT READY**. A methods
review must document pilot adequacy; a computed percentile alone is not sufficient evidence.
No default pixel tolerance is supplied by historical edits.

A candidate matches only if independently resolvable outer-rim identity is compatible,
`hypot(cx-ref_x, cy-ref_y) <= Tc` AND `abs(r-ref_r) <= Tr`, using unrounded display geometry.
Reference is adjudicated geometry (for agreed passes, arithmetic mean of the two geometries);
retain originals and uncertainty. Centre agreement alone never matches a concentric inner rim.
If the tolerance envelope also admits an independently labelled different rim/object and
identity cannot disambiguate it, mark membership unresolved, not true. Resolve identity against
the pre-output scene/rim inventory; later candidate inspection cannot redefine target truth.
Retain every matching raw index (duplicates count once per case for recall). Final emitted
geometry is matched separately at its actual returned precision; no half-pixel adjustment.
Per-case uncertainty exceeding the frozen pilot-supported envelope remains unresolvable, with
counts; do not widen tolerances case by case after viewing outputs. Sensitivity summaries may
show pilot-derived uncertainty bounds if frozen beforehand, never optimize them on results.

## Frozen metrics, denominators and failure attribution

Primary unit is one automatic-selection decision per clip, one predetermined frame. Freeze N
and the ordered manifest before execution, with no early stopping on success/failure. Multiple
clips from one session are clustered; repeated/adjacent frames, crops and timing repeats are
not independent trials. Additional frames, if separately approved, are secondary clustered
diagnostics with clip/session IDs, never extra independent N.

Keep mutually exclusive policy groups: selectable unique supported pre-movement target S;
required-refusal R (including ambiguity/no-target/no-pre-movement); unsupported U (reported
separately even when refusal required); indeterminate I. Resolve overlapping reasons with known
unsupported U first, then independently established required-refusal R, then I for unresolved
policy/eligibility or an unresolved reference for a potentially selectable case, then S. Missing
unique target geometry never overrides a known R/U policy: agreed identity ambiguity is R, and
an emitted selection there is unsafe acceptance. Retain all reason flags. Report total inventory
and each group/exclusion/error so all rows reconcile. R/U/I never earn positive recall.

For every S case, report raw-Hough membership, post-first-60 membership, post-fit membership,
and ranked top-1/3/5/10/20 membership. Each rate uses **all S**, including zero/fewer-than-K
candidates and execution failures. For fewer than K test all available; report availability
counts (0, 1, <3/<5/<10/<20) separately. Errors are unsuccessful observed attempts with explicit
error counts, not evidence of no raw Hough proposal; lost/unavailable instrumentation invalidates
stage attribution and blocks a scientific conclusion. Missing/unresolved membership is reported
as such with conservative lower/upper recall bounds, never quietly removed to improve rates.

Separately count final current-algorithm correct selection, wrong selection and refusal on S;
correct refusal/unsafe emission on R and U; emitted/refused on I without correctness. Report
pre-refinement top-1 correctness independently of final correctness. A correct top candidate
can drift to a wrong rim or fail support/bounds/rounding in refinement. Preserve each such
final-stage failure, including wrong-to-correct changes, without calling it ranking success.
Report numerator, denominator and 95% Wilson interval for each estimable binomial metric;
N=0 means unavailable. Show challenge/session strata and missing-reference counts.

True-target miss attribution is a nested funnel, per K: no raw match; raw match but none in
first 60; retained match but none passes fit; post-fit match but lowest matching rank >K;
matching rank <=K. Separately flag refinement/final-stage failure; these flags may coexist
with the stage funnel and are not summed as disjoint cases. Record first matching rank and all
matches. Never label runtime/instrumentation error a geometric miss.

Wrong top-1 taxonomy: another plate on target barbell; wrong concentric rim; another loaded
barbell; plate storage; rack/equipment; background circular object; other (description);
unknown/unresolvable. Assign from independent scene inventory and retain unknown. Required
refusal/unsupported/indeterminate cases remain distinct, even when a distractor class is known.

Margin is unrounded pre-refinement `support[0] - support[1]`, ties zero, <2 candidates
unavailable. Retain paired margins/outcomes, n/missing, median, quartiles, range and empirical
distributions for correct top-1, wrong top-1, ambiguous/required-refusal, unsupported and
indeterminate separately. It is descriptive only: no fitted threshold, ROC-selected operating
point, calibrated probability or production acceptance rule from this evaluation.

## Precision and exact interpretation rules

Use two-sided 95% Wilson intervals, z=1.959963984540054. For p=x/n, denominator
`d=1+z*z/n`, centre `(p+z*z/(2*n))/d`, half-width
`z*sqrt(p*(1-p)/n+z*z/(4*n*n))/d`. Examples, percent:

| Independent N | Observed 100%: 95% interval | Observed 90%: 95% interval |
| ---: | ---: | ---: |
| 20 | 83.89–100 | 69.90–97.21 |
| 30 | 88.65–100 | 74.38–96.54 |
| 50 | 92.87–100 | 78.64–95.65 |
| 100 | 96.30–100 | 82.56–94.48 |
| 200 | 98.12–100 | 85.06–93.43 |
| 500 | 99.24–100 | 87.06–92.33 |

These are precision scenarios, not a recommendation for N or an invented 98–99% gate. Even
20/20 cannot establish near-perfect recall. For zero misses the Wilson lower bound is
`n/(n+z*z)`; a desired lower bound L requires `ceil(z*z*L/(1-L))` independent successes.
For anticipated p and desired half-width h, use the formula above to select N before collection
(the rough planning approximation is `z*z*p*(1-p)/h**2`). Freeze desired precision, anticipated
p/range, target challenge coverage, attainable independent N and rationale before scoring.
If no useful precision is affordable, state that before running; no powered architectural
discrimination is promised. Multiple top-K intervals are descriptive pointwise intervals,
not simultaneous inference or independent hypotheses.

Use K=20 as the fixed largest-set attribution, with all smaller K reported; never choose K
after seeing results. Partition all S into top-1 member, rank 2–20 member
(ranking opportunity), post-fit rank >20 (ranking budget loss), fit loss, truncation loss,
raw-generation loss, unresolved/error. Report these paired counts over S and conditional
stage denominators. `recall20 - recall1` is the paired ranking opportunity fraction;
`1 - raw_recall` is the raw-generation deficit, not `1 - recall20`. Intermediate losses cannot
justify a detector. Do not subtract marginal confidence intervals as if independent.

Exact permitted conclusions: zero fully identifiable S cases or any incomplete prerequisite
means **insufficient evidence**. With complete evidence, nonzero ranking-opportunity count
demonstrates observed ranking failures; nonzero raw-generation-loss count demonstrates observed
proposal failures; both means mixed observed failures; neither means neither observed in this
sample. Give magnitudes/intervals and all intermediate losses. These statements attribute
observed cases only. The stronger labels “very frequent,” “materially worse,” “ranking problem
dominates” or “proposal problem dominates” require owner-approved, pre-scoring recall/gap/loss
limits and sufficient precision; none is selected here. If those scientific discrimination
limits and attainable N remain unspecified, architectural discrimination is **insufficient**
even after descriptive scoring. No Prompt 2 or architecture promotion follows from any result.

Wilson coverage assumes independent Bernoulli cases. Report session/source counts and per-session
results; pooled clip intervals with shared sessions are conditional descriptive summaries and
overstate population precision if treated as independent. Do not inflate N with nearby frames.
If independent source/session cases cannot be collected at the planned precision, record the
shortfall before execution, rather than calling a larger correlated frame count adequate.

## Private freeze supplement and readiness checklist

Keep the prescribed [freeze location/template](../validation/PLATE_SELECTION_FREEZE_RECORD.md):
`validation/private/frozen-eval/freeze/<freeze-id>.freeze-record.json`. This protocol supplements
it; it is not an actual record. For the chat decision bind the committed parent plan and its
SHA-256 plus decision heading as authority evidence; no fabricated issue-comment URL is needed.

Add a `prompt1` research section containing protocol revision/commit/hash; harness source hash;
full Hough/config dump/hash; exact commands/API executor; Python/OpenCV/NumPy versions and
environment identity; source and frame hashes/PTS/orientation; both annotation-pass hashes and
adjudication; pilot hashes/Tc/Tr derivation/adequacy review; scene inventory; case grouping;
N/precision rationale; K list; all metrics/denominators/exclusions/failure taxonomy/margin rules;
decision mode/limits; exact resource/time/repeat budget; stop rules; independent review evidence.
Truth files stay separated from candidate execution inputs. Keep raw and final observations,
membership decisions and all errors privately with the freeze hash. Hash code/config/inputs
before and after. No tuning after scoring; revisions burn unseen status under the existing rules.

- [ ] Owner decision and unchanged main baseline bound; no later-prompt authorization inferred.
- [ ] Existing source inventory audited; eligible independent sources, rights, novelty, roles,
      source/frame hashes and session grouping verified, with additional capture only for gaps.
- [ ] Frame rule/PTS schedule and usable timing context fixed without candidate or onset leakage.
- [ ] Two independent blind passes per case plus blind reconciliation retained and hashed.
- [ ] Independent centre AND outer-radius/rim repeatability pilot complete; Tc/Tr numeric,
      coverage/precision justified, concentric discrimination checked before any output visibility.
- [ ] N, precision/coverage rationale and exact run limits fixed; insufficiency acknowledged
      when stronger hypotheses cannot be distinguished.
- [ ] Full private freeze completed with all hashes, commands, environments and Prompt 1 fields.
- [ ] Future executor refuses incomplete/mismatched freezes and excluded roles before decode;
      array API alone does not satisfy this gate.
- [ ] Synthetic tests verify unchanged baseline return, raw order, first-60, all fit flags,
      ranked score/order, duplicate association, refine/final failure and hook restoration.
- [ ] Independent code/research-method review completed and findings addressed; targeted
      verification evidence retained. Tool/protocol verification is separate from data readiness.

Remaining readiness gaps: verify existing media/rights/manifests and source/session eligibility;
fix the exact acquisition frame/PTS rule; verify or complete two independent blind
identity/policy/geometry/onset label passes and adjudication; establish independent centre/radius
repeatability and numeric justified matching tolerances;
sample precision/coverage and actual collection/run budget; complete hashed private freeze;
and a reviewed fail-closed frozen-data executor. Unverified prerequisites prevent a formal scored
run; they do not establish that the recordings or labels are absent from the primary checkout.
This preparation does not manufacture evidence, derive it from historical edits, or create
an empty record that appears complete. Stop at **READY-BUT-NOT-SCORED** and report these gaps.

## Preparation verification (2026-10-15)

The ten new observation tests failed before the harness existed, then passed. The full
`python -m unittest discover -s research/vbt-workflow/tests -p 'test_*.py'` run passed
606 tests (10 skipped). The validation suite passed 337 tests (one skipped), and
`python validation/tools/schema_check.py` passed all 15 mapped documents. Synthetic
`--self-check` passed. The source diff confirms `vbt_suggest.py` is unchanged.

The existing local environment is Python 3.11.9, OpenCV 5.0.0, NumPy 2.4.6; no dependency
was installed or changed. Historical OpenCV 4.12.0 evidence is not interchangeable with this
environment. All 26 relative document links resolve; Wilson examples were independently
recomputed. Independent review approved the observer and identified a policy-group precedence
issue in this protocol; the correction preserves known required-refusal cases even without
unique geometry. These checks establish preparation behavior only, not target-selection recall.
Rust gates were not run for this separate Python research/documentation change.
