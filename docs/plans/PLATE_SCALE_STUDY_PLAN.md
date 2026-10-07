# Frozen plate-scale diagnostic study (#88)

2026-10-06. Freeze before computing multi-frame estimates or their reference agreement.
Existing seed-only stick comparisons were already inspected during roadmap analysis;
this is a predeclared development study, not blind or held-out validation.

## Goal and scope

Evaluate one deterministic multi-frame scale estimator against existing confirmed filmed-stick
references. Use all eight development fixtures in `validation/private/vbt/manifest.json`:
`vbt-e169ecc156007a9e`, `vbt-45854c8cdf352a6f` (clean), `vbt-bb101cea545505fc`,
`vbt-a8cd0fb856ab83ca` (snatch), `vbt-4eb16d0c845e4bea`, `vbt-e2d13139fc682295`,
`vbt-017cea944dfbb194`, `vbt-ad613d6b13787901` (squat).
Reuse retained SAM 2.1 bplus-circle predictions/geometry/canonical analyses and the
2026-10-03 confirmed session's stick clicks/packages. No tracker/decoder rerun, model,
installation, human task or held-out access. Probe actual source PTS with the existing helper.

## Estimator and support

Exclude initialization. Radius evidence must be timestamp-aligned with a tracked prediction,
`fit_attempted=true`, `accepted=true`, no rejection reason, coverage >=18 of 36 bins,
positive inlier count and finite positive fitted radius. Rejected/fallback/lost samples
are counted but never used. Require >=10 eligible observations, >=50% of post-seed samples,
and >=0.5 s eligible timestamp span. These are research sufficiency rules, not M0 gates.
Unsupported cases retain counts/reasons and no recommended scale or fabricated band.

Use the median eligible radius R: diagnostic scale = recorded physical diameter / (2R).
Use nearest-rank radius p10/p90: empirical scale band = [diameter/(2 p90), diameter/(2 p10)].
It describes central frame-to-frame apparent-size spread, not a confidence interval or a
calibrated probability of physical-scale coverage. Correlated frames, shared segmentation
bias, depth/perspective, blur and physical-diameter uncertainty are not accounted for.

Report eligible/rejected/lost/initialization support, diameter median/p10/p90/min/max,
seed-radius deviation from median (%), scale/empirical band, and descriptive OLS diameter
slope against actual timestamp (px/s). No stationary/jitter or causal claim follows from
this slope. No new filtering, radius grid, confidence weighting or outlier trimming.

## Reference comparison and decision

Recompute stick reference with existing `scale_reference` parsing/binding/measurement helpers
from confirmed clicks, including its +/-1 px endpoint precision model. Validate media hashes,
manifest/development roles, package/display/frame/PTS binding, prediction contracts, sidecar
identity/provenance/exact timestamp alignment and original canonical analysis/seed geometry.
Use canonical recorded plate scale unchanged as the seed-only comparator. Validate calibration
in the authoritative Rust consumer during the real study; research does not replace domain logic.

Per video report signed/absolute relative scale error (%) against stick point estimate for
seed-only and multi-frame scale; whether the empirical band contains the point estimate,
overlaps the reference interval, and contains the entire reference click-precision interval.
Also report error ranges across the reference interval. Stick geometry/depth uncertainty
remains unquantified; neither scale agreement nor this band establishes velocity accuracy.

Continue this configuration as a research candidate only if all eight cases are supported,
equal-video mean absolute relative error improves, >=6/8 videos improve strictly, none worsens
by >1 percentage point, and every empirical band contains its full reference interval.
Otherwise REJECT the tested recommendation/band configuration. Preserve all case outcomes;
never tune after scoring or claim production promotion from this development sample.

## Implementation, verification and stop

Add a stdlib-only diagnostic in `research/bar-path-measurement`, reusing `study_io` and
`scale_reference`. Freeze exact cases/input/source/plan hashes before scoring. Retain the
producer's existing config/version and private references; diagnostics have their own private
format/version. Do not change `analysis-v1`, calibration methods, schemas, golden files or
production defaults. Existing raw observations, coordinate-free loss, timestamps and provenance
remain intact. Private JSON is generated only under git-ignored `validation/private`.

Tests cover a known scale/spread/time trend, reciprocal-band orientation, accepted-only support,
low/empty coverage, finite and identity/PTS checks, unchanged inputs, deterministic reports,
reference uncertainty and failed decision criteria. Reuse #103 cleanup/retry behavior unchanged.
Run research/validation/schema checks, one diff-first independent review, canonical validation
of all consumed analyses, and two frozen-input study passes with byte comparison and original
input rehash. Keep source snapshots and portable replay; deliver hash-verified private artifacts
to the owner's project folder. Publish aggregate findings and a focused PR linked to #88.

Stop after this one estimator/band and its repetitions. A failure is evidence, not permission
to tune. No SAM trace, fusion, WL agreement switch, M1 feature or production calibration change
is part of this PR. Update the roadmap with this result without closing unmet M0 evidence gates.
