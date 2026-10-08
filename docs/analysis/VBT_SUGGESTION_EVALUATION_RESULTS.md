# Retained confirmed suggestion evaluation (#112)

Work type: **evaluate evidence**. Eight retained owner-confirmed development clips were evaluated
under [rules frozen before calculation](VBT_SUGGESTION_EVALUATION_RULES.md), commit
`1f051d15b45e3b671acf6a51e07504255c67af9a`. This completes the descriptive retained-input batch;
it supplies no automatic-acceptance threshold, independent accuracy verdict, or #113 proceed
decision. Target-identity and explicit rejection labels are not derivable from these records.

## Inventory and exclusions

The selected personal sessions directory contains one retained session/page, eight confirmed
clips, and eight unique source-video hashes. Each of the four items has eight proposed and eight
adjusted rows. Accepted, manual, skipped, no-suggestion and unavailable-confirmation counts are
all zero for this retained set. There are no excluded sessions or clips. The zero missing counts
describe this inventory only; they are not an estimate of failure rates on other videos.

Confirmation provenance is the exact retained #95 session-input CSV, validated with the existing
session contract and bound to the page/state and completed session record. Each source was
hash-verified against that record, and its personal manifest entry has `purpose: development`.
Original filenames, coordinates, private video bytes and pixels are not published here.

No #111 assessments were retained for these clips: processing, mechanical validity, and experiment
suitability are separately **unknown for all eight**, and independent accuracy is **not_established
for all eight**. As predeclared, that does not exclude a descriptive comparison of the retained
seed-frame proposals with their confirmed edits. It cannot qualify their tracking or kinematics.

## Geometric discrepancies

All groups below are adjusted (`n = 8` each). Accepted and manual groups remain empty, with null
statistics; there are no accepted-by-construction zeros in these distributions. Point discrepancies
are Euclidean distances in display-oriented pixel-centre coordinates. Signed discrepancies use
proposal minus confirmed, with confirmed geometry as the relative denominator.

| Item / metric | Mean | Median | Minimum | Maximum |
|---|---:|---:|---:|---:|
| Plate centre distance (px) | 2.0176 | 1.5698 | 0.5825 | 3.8515 |
| Plate radius absolute discrepancy (px) | 1.1712 | 1.1250 | 0.2300 | 2.2300 |
| Plate radius signed discrepancy (px) | +1.1137 | +1.1250 | -0.2300 | +2.2300 |
| Plate radius signed relative discrepancy (%) | +0.6242 | +0.6309 | -0.1284 | +1.2502 |
| Low marker distance (px) | 4.6053 | 4.6657 | 2.6456 | 6.4005 |
| High marker distance (px) | 5.2776 | 5.5203 | 2.0800 | 7.5448 |
| Stick length signed discrepancy (px) | -2.1110 | -2.5683 | -7.7106 | +3.8260 |
| Stick length signed relative discrepancy (%) | -0.2009 | -0.2465 | -0.7324 | +0.3645 |

The report retains every item discrepancy privately, not just its aggregate, and the suggestion's
method, parameters, heuristic confidence and identifier. The existing methods are
`plate-hough-edge-v1` and `stick-yellow-markers-v1`; retained environment is OpenCV 4.12.0 and
NumPy 2.2.6. This evaluator uses only the standard library and does not invoke either suggester.

Target mismatch counts and explicit suggestion-rejection counts are **null / not_derivable**
for each item. #95 records adjusted coordinates without a wrong-object judgment; neither an edit
nor a large distance supplies such a label. An absent rate is not a zero error rate.

## Reuse of #95 evidence

The [published #95 spot-check aggregates in PR #96](https://github.com/Szczepanov/openbar/pull/96)
are retained as the historical comparison. Its plate-centre median/mean/max 1.6/2.0/3.9 px,
radius median +0.63% and range -0.13% to +1.25%, low-marker median/max 4.7/6.4 px,
high-marker median/max 5.5/7.5 px, and stick-length median -0.25% and range -0.73% to +0.36%
all agree with this batch at the PR's displayed precision. Its 8/8 proposals per kind and
32/32 adjusted items also agree.

The PR's prose supplies no exact vectors, frozen rules, input-set hash, explicit identity labels,
missing/rejected denominators or #111 assessments. Its numbers are not a second dataset and are
not silently merged into the new summary. The current batch extends the audit trail and
denominator reporting; it does not turn those spot checks into independent truth.

## Reproduction and retained evidence

Run from this branch; `--data-root` can point to the existing checkout retaining owner inputs.
Use a new output name for each pass. The private JSON has research format
`openbar-research-vbt-suggestion-evaluation`, version 1; it is not an Analysis or consumer package.

```powershell
python research/vbt-workflow/evaluate_suggestions.py `
  --data-root <retaining-checkout> `
  --sessions-root <retaining-checkout>/validation/private/vbt/sessions `
  --rules-commit 1f051d15b45e3b671acf6a51e07504255c67af9a `
  --output target/suggestion-evaluation-v1.json
```

Private report: `target/suggestion-evaluation-final-v1.json`; its repeated pass is
`target/suggestion-evaluation-final-repeat-v1.json`. These remain ignored. Both passes produced identical
bytes, SHA-256 `8647526049b2a96487f4c0319a1002282f0fcc89c84cd1228a152db42de5beb1`.

| Bound evidence | SHA-256 |
|---|---|
| Frozen rules blob | `25b9572af7560a19a4ddcbd4c30b954d255b6202ce67f9177ccc24f297c1968b` |
| Retained session state | `2e95077278b8797af0d604a0c3f2cf4ad0645cde1c70b5971701a2eceefd667a` |
| Retained confirmation CSV | `d72c13a2e850aca81c5b3f03430a9fbe50eca607f05a3fb3a9d512f9070bc966` |
| Retained completed session record | `848af558e8084dbd6e85014c17980878a3c46a2a31eaa0cd1e8e5bff520cd2c6` |
| Inspected personal manifest | `7193a9dbc5646bcc6a6795536d88fb5c185c05237d78889b1e63eaf0ba6ac6e0` |

Every source-video SHA-256, clip/package/frame/page binding, and evaluator SHA-256 is retained
in the private artifact, along with hashes of the shared validation modules. Unrecognized private
metadata is hashed rather than copied; known method parameters and environment versions remain
inspectable. Inputs were rehashed before returning. No media, private labels,
per-clip coordinates or original filenames are committed.

Fresh verification: 235 workflow tests ran successfully (four opt-in E2E/tool tests skipped),
211 validation tests passed, committed schema catalogue passed for 13 documents, Python compilation
and `git diff --check` passed. The 15 focused evaluator tests include missing/failed/skipped rows,
per-item groups, exclusions, source binding, unsupported-version inventory, confidence validation,
provenance privacy, assessment rejection/binding, non-finite statistics and deterministic output.
Independent diff-first review approved the final implementation after red/green regressions for
manifest-source binding, unsupported-version denominators, confidence and metadata handling.

## Conclusions and limits

The retained proposals were close to the owner's edits on these eight displayed seed frames,
and the complete batch reproduces the historical aggregates at their published precision.
All items still required edits. Owner confirmation after seeing suggestions can introduce
confirmation bias; this is neither blind ground truth nor held-out validation. Empty accepted,
manual, skipped and failed groups have test coverage but no real observations in this inventory.

This set cannot quantify target-identity mistakes or explicit rejection, choose general error
thresholds, validate recording support, establish physical accuracy, calibrate heuristic confidence,
or authorize human-out-of-loop initialization. A reviewed proceed/stop decision remains required
before #113; #57, #53/#58/#59 and #79 remain separate evidence tracks.
