# Retained VBT suggestion evaluation rules (#112)

Work type: **evaluate evidence**, retained development inputs only. These rules must be
committed before discrepancy calculation. The evaluator records that commit and this file's
SHA-256. A changed rule requires a new freeze, not retroactive relabeling of a result.

## Frozen set and confirmation provenance

Inventory every retained `session.json` under the explicitly selected personal VBT sessions
directory. At inventory time the owner's checkout contains one session, eight confirmed clips,
and no skipped clips. Its CSV records all 32 items as adjusted. Do not create new confirmations,
regenerate suggestions, use #57 held-out inputs, or tune the existing #95 suggester.

Bind exact session-state, session-input CSV, and session-record bytes with SHA-256. Reuse
`session_ingest.compute_page_id` and `session_contract.parse_session_csv` for page binding,
identity, geometry, and accepted/adjusted/manual/skipped validation. Verify the completed session
record's input hashes, session/page identity, per-clip source/suggestion/status binding, and the
personal manifest's `purpose: development`. Hash retained source media without decoding or
publishing it. Missing confirmations, changed bindings, unavailable source bytes, unsupported
formats, or non-development inputs are exclusions, with explicit reasons and denominator counts.
Do not treat excluded sessions as absent from inventory. Duplicate session/page identities are
refused; multiple pages of the same video remain distinct confirmation occasions, identified by
source hash, package, frame, and page (not independent samples).

Use #111's separate assessment states if retained: validate its schema and clip/source/seed/run
binding before consuming it. A rejected assessment excludes the clip from eligible discrepancy
summaries but remains in inventory/status counts. An absent assessment means unknown mechanical
validity and unknown experiment suitability, not a pass. Unknown assessments may enter this
descriptive **seed-frame confirmation comparison**: the experiment measures differences between
two retained proposals/clicks, not tracking/kinematics or physical accuracy. Report unknown counts
separately. Never change canonical Analysis or promote suitability/accuracy.

## Denominators and statuses

Each inventory clip contributes one slot for each of `plate_center`, `plate_radius`, `stick_low`,
and `stick_high`. Report confirmed, skipped, excluded and unavailable-confirmation counts, plus
the actual accepted/adjusted/manual status counts. Status counts include excluded confirmed rows
when their confirmation was successfully validated. Exclusions never become measured zeros.

For each item, a retained `status: failed` suggestion is **no_suggestion**, with the recorded
method/reason. A retained `status: suggested` proposal is **suggested**. A missing/unrecognized
suggestion record is **not_derivable**, never failed/rejected. #95 records no explicit
suggestion-rejection decision; adjusted does not establish rejection. Report rejected counts as
null/not_derivable, including on skipped clips. Skipped is a whole-clip decision, not evidence
that a particular suggestion was wrong.

Target identity means whether the proposal selected the owner's intended plate or named stick
marker. #95 records coordinates and edits, not explicit wrong-object/wrong-marker judgments.
Report target mismatch as **not_derivable**, with null mismatch count. A changed coordinate,
large discrepancy, suggestion identifier mismatch, or skipped clip is not an object-identity
label. Identifier/binding mismatches are exclusions. No geometric threshold is an identity rule.

## Geometric discrepancies

Use display-oriented pixel-centre coordinates (ADR-0007), exactly as retained. Do not apply a
half-pixel shift. Use the proposal rounded to two decimals by the existing session contract,
matching the page/CSV comparison; accepted rows therefore have zero discrepancy by construction.
For validated, non-excluded, confirmed rows with a usable suggestion:

- Point discrepancy in px: Euclidean distance from proposal to confirmed point, separately for
  plate centre, low marker, and high marker.
- Plate radius: signed discrepancy `proposal - confirmed` in px, absolute discrepancy in px,
  and signed relative discrepancy `100 * (proposal - confirmed) / confirmed` in percent.
- Stick length (secondary comparison): Euclidean high-to-low distance in px for each set;
  signed relative discrepancy uses confirmed length as denominator. This is a geometric
  comparison, not a scale/velocity recalculation.

Report sample count, arithmetic mean, median, minimum and maximum, with null statistics for
empty groups. Use sorted observations and `math.fsum`; no interpolated quantiles or confidence
probabilities. Separate accepted from adjusted and manual items. Manual items have no proposal,
so no geometric discrepancy is calculable. Secondary stick-length groups are accepted only if
both endpoints were accepted; otherwise adjusted, or unavailable when either proposal is absent.
Retain deterministic per-item discrepancy rows privately so distributions can be audited.

## Existing #95 aggregates and comparability

Reuse the published spot-check evidence from [merged PR #96](https://github.com/Szczepanov/openbar/pull/96).
It reports eight proposals for both kinds, all 32 items adjusted, plate-centre median/mean/max
1.6/2.0/3.9 px; radius signed-relative median +0.63%, range -0.13% to +1.25%; low-marker median/max
4.7/6.4 px; high-marker median/max 5.5/7.5 px; stick-length signed-relative median -0.25%, range
-0.73% to +0.36%. These are retained **published aggregates**, not new independent observations.

The PR supplies no exact vectors, input-set digest, frozen rule commit, missing/rejected
denominators, explicit identity labels, or mechanical assessments. Compare today's independently
specified calculation with those rounded numbers, identifying unavailable bindings and any
disagreement. Never silently call the calculations equivalent or merge their denominators.

## Evidence artifact and limits

Write a separate versioned research JSON report, deterministically ordered by session/page,
clip order and item name; no time, original filenames, source paths, media pixels or absolute
machine paths. Include source hashes, suggestion methods/parameters/environment and IDs,
confirmation provenance, exclusions and assessment categories. Rehash read evidence before
returning; refuse overwrites. Repeat unchanged inputs to prove byte identity. Publish aggregate
results and artifact SHA-256 only; retain item-level evidence under ignored `target/`.

Owner-confirmed development inputs can contain confirmation bias. Accepted rows are zero by
construction; adjusted rows are edits relative to displayed proposals, not blind labels.
This experiment cannot establish held-out performance, target-identity error rate, independent
physical accuracy, calibrated confidence, or automatic acceptance thresholds. #113 still needs
a separately reviewed proceed/stop decision; no automatic initialization or production promotion
is authorized by this report.
