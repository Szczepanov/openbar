# CSRT-gray S4/S5 implementation plan: tracker state, explicit loss, synthetic benchmarks

Status: implemented, 2026-10-13. Not an architecture decision.
Issue: #176 (S4 and its synthetic S5 proof form one deliverable). Refs #57, #80; does not close #57.
Parent: [`CSRT_GRAY_TRACKER_IMPLEMENTATION_PLAN.md`](CSRT_GRAY_TRACKER_IMPLEMENTATION_PLAN.md) (sections 3-5 define the
method, parameters and slice outline; this document adds the code-level breakdown, the
rulings the parent left open, and the points where the parent disagrees with the code on main).
Prerequisites merged: #171 (S1 FFT/math), #174 (S2 patches), #182 (S3 filter). Base: `origin/main` at `d8144a3`.

This is the **implement experiment** step. Evaluating evidence on real video (#57) and phone
runtime (#80) are separate decisions and are not authorised by this plan.

## 1. Findings that change the plan

Checked against main; each needs a ruling before code (recommendation first).

| # | Finding | Recommended ruling |
|---|---------|--------------------|
| F1 | **Core has no false-track metric.** `TrackerMetrics` reports error, availability, loss and low-confidence counts. `GroundTruthSample` is a centre only, and predictions at timestamps with no truth sample are ignored. #176 asks the core adapter to compute "false-track metrics". | Use the evaluator unchanged for error/availability on frames where the target is present. For absent-target frames (occlusion, exit) assert the observation state directly in the test: a `Tracked`/`LowConfidence` observation while the target is absent is a false track. That is a count in test code, not a new metric formula. Adding a visibility-aware metric to core is out of scope ("alternate metrics"); note it as a follow-up for #57. |
| F2 | Parent S2 note says to reword `TrackerError::InsufficientSeedContrast`. #176 also requires existing template/contrast outputs to stay unchanged, and the old text is quoted in `M0_PRIVATE_EVIDENCE_REPORT.md`. | Do not touch the existing variant or message. Add `InsufficientSeedPatchContrast { rms_levels: f64 }` for the CSRT seed. |
| F3 | `filter::CsrtGrayConfig` (S3) holds only the five training fields and is `pub(super)`. The public config needs eleven fields. | Rename the S3 struct to `FilterTrainingParams` (mechanical, S3 tests only) and define the public `CsrtGrayConfig` in `tracker.rs` with a `training_params()` accessor. Avoids two structs with one name. |
| F4 | Ambiguity rule ("equal competing maxima") compares floats for equality. Real distractors rarely produce bit-equal maxima. | Keep exact equality, tested on hand-built arrays. In synthetic distractor scenarios expect PSR collapse, not an ambiguity flag, to cause the loss. Record this limitation in the experiment doc. |
| F5 | Parent leaves open what happens when the new-anchor crop is unavailable or flat after a tracked frame (no sample to learn from). | Emit the tracked observation, skip the model update, move the anchor. No error. The next frame's crop at that anchor then yields an ordinary loss. |
| F6 | Parent leaves seed observation state open ("self-response confidence"). | Seed observation is always `Tracked` (human-authored position) with confidence and `quality_score` from the self-response PSR. A seed whose self-response is not a clean peak at (0,0) with PSR >= `lost_psr` is rejected with a new `SeedNotTrackable { psr: Option<f64> }`. |
| F7 | Reported centre. The anchor is integer, the seed centre may be fractional. | Reported centres after the seed are the integer anchor (parent: integer-pixel tracking). Seed observation keeps the authored centre/bounds exactly. Add one fractional-seed test pinning the up-to-0.5 px offset. |

If you disagree with any ruling, change it here before S4 starts; the test list below follows them.

## 2. Scope

In: public `CsrtGrayConfig` / `CsrtGrayTracker` implementing `ManualSeedTracker`; peak search and
PSR; loss and adaptation policy; identity; generated-sequence tests; core-benchmark integration;
experiment docs; the three new `TrackerError` variants.

Out (unchanged from #176 and parent section 2): CLI, decoder, mobile, detector, HOG, adaptive
segmentation, scale/rotation, global recovery, alternate metrics, held-out or #79 data, new
dependencies, schema/version/golden/fixture changes, any change to template/contrast behaviour.

## 3. Design

### 3.1 Files

| File | Change |
|------|--------|
| `csrt/peak.rs` (new) | `locate_peak` over a response array: decode, search domain, ties, ambiguity, PSR. Pure functions, hand-testable. |
| `csrt/tracker.rs` (new) | `CsrtGrayConfig`, `CsrtGrayTracker`, identity, `TrackState`, `ManualSeedTracker` impl. |
| `csrt/filter.rs` | Rename config struct (F3); add `blend_filters(old, candidate, learning_rate)`. |
| `csrt/mod.rs` | Declare modules; register `peak.rs` and `tracker.rs` in `SOURCES` (5 -> 7) and keep one test module per file; remove `expect(dead_code)` attributes as items become consumed. |
| `lib.rs` | `pub use csrt::{CsrtGrayConfig, CsrtGrayTracker};` three new `TrackerError` variants + `Display`. |
| `tests/csrt.rs` (new) | Generated sequences and benchmark integration. |
| `docs/validation/TRACKER_EXPERIMENTS.md`, parent plan | S4/S5 contract, parameters, measured synthetic results, limitations, slice status. |

`peak.rs` and `tracker.rs` are split so neither approaches the repo's file-size ceiling and the peak
rules are testable without images.

### 3.2 Peak and PSR (`peak.rs`)

```rust
pub(super) enum PeakOutcome {
    Peak { dx: i32, dy: i32, peak: f64, psr: f64 },
    Ambiguous,        // exact-equal maximum outside the exclusion square
    FlatSidelobes,    // empty sidelobe set, std <= 1e-12
}
pub(super) fn locate_peak(response: &[f64], side: usize, exclusion_radius: usize)
    -> Result<PeakOutcome, NumericError>;
```

- Bin `i` decodes to `i` if `i < side/2`, else `i - side`. Search only `|dx|,|dy| <= side/4`.
- Highest response wins. Tie order: smaller `dx*dx+dy*dy`, then `(dy, dx)` lexicographic, independent of scan order.
- Ambiguity: any other cell of the **whole** response, outside the wrapped `(2r+1)^2` exclusion square around the selected peak, with response exactly equal to the peak.
- PSR = `(peak - mean) / std` over all cells outside that square, population std, two-pass, row-major, `sqrt` only (allowed by the scan; no `hypot`/`exp`).
- A non-finite derived value is `NumericError::NonFiniteOutput`, never a score.

### 3.3 Config and identity (`tracker.rs`)

`CsrtGrayConfig` (Copy, Clone, Debug, PartialEq, Default) with the parent's table:
`lambda 0.01`, `admm_iterations 4`, `mu_initial 1`, `mu_growth 3`, `mu_max 81`, `learning_rate 0.02`,
`label_sigma_px 2`, `lost_psr 5`, `tracked_psr 8`, `psr_exclusion_radius_px 2`,
`seed_timestamp_tolerance_s 0.0005`.

`try_new` validates: training fields through the S3 validator (mapped to
`TrackerError::InvalidConfiguration { field }`), `learning_rate` in `[0,1]`, `label_sigma_px` finite and positive, seed tolerance
through the shared `validate_seed_tolerance` (finite, `>= 0`), `0 < lost_psr < tracked_psr`, exclusion radius in `1..=3`
(below half the minimum 8 crop).

Identity: id `csrt-gray-v1`, implementation `csrt-gray`, version `1`. `config` (a `BTreeMap`) records every
effective parameter plus the fixed constants: patch side rule (min 8, max 512), search limit `side/4`,
flat-patch threshold (1 intensity level), feature = mean-subtracted unit-RMS periodic Hann, support = disk of
seed radius, integer-pixel tracking, no scale. `confidence_semantics`: `max(psr,0)/(max(psr,0)+tracked_psr)`,
algorithm-specific, not a probability, not comparable across trackers. Floats formatted with `to_string()` like the
existing trackers.

### 3.4 State and loop

Private `TrackState { anchor: (i32,i32), filter_fft: Vec<Complex>, was_lost: bool }` with a `step()` that the
unit tests can drive and inspect (needed to assert "model unchanged" directly, per parent S4).

Seed (frame `seed_index` from `validate_sequence_and_seed`; earlier frames ignored, as in the existing trackers):

1. `side = patch_side(radius_px)`; `InvalidRadius`/`RadiusTooLarge` -> `InvalidSeedRadius`, other `NumericError` -> `CsrtNumerical`.
2. `Fft2d::try_new(side, side)`, `label_fft`, `support_mask` built once per run.
3. `normalized_patch` at `rounded_point(seed centre)`: `OutsideFrame` -> `SeedTargetOutsideFrame`;
   `InsufficientContrast{rms}` -> `InsufficientSeedPatchContrast`.
4. Forward FFT, `train_filter`, self-`response`, `locate_peak`. Require `Peak` at (0,0) with `psr >= lost_psr`, else `SeedNotTrackable`.
5. Seed observation: authored centre and `bounds_for_center`, `Tracked`, mapped confidence, `quality_score = Some(psr)`, displacement 0.

Each later frame, in this order (bounds precede any update):

1. Patch at `anchor`. `OutsideFrame` -> lost `NoCandidate`; `InsufficientContrast` -> lost `InsufficientContrast`; `NumericError` aborts.
2. Forward FFT, `response`, `locate_peak`. `Ambiguous`/`FlatSidelobes` -> lost `PoorMatch`, `quality_score None`.
3. `psr < lost_psr` -> lost `PoorMatch`, `quality_score Some(psr)`.
4. Candidate anchor = anchor + (dx,dy) with checked arithmetic; centre/bounds from it; `!bounds_fit` -> lost `TargetOutsideFrame`.
5. Classify: `psr >= tracked_psr` -> `Tracked`, else `LowConfidence`. Move the anchor. `reacquired_after_loss = was_lost`.
6. Only if `Tracked` and the crop at the new anchor in this frame is available: train a candidate filter there and `blend_filters`. Never on `LowConfidence` or lost frames.
7. `was_lost` follows the existing trackers (set on loss, cleared on any accepted observation).

Lost observations go through the shared `lost_observation` helper: no centre, no bounds, no confidence. Lost frames freeze both
anchor and model. A target that returns within the retained search region is reacquired; one that returns beyond `side/4` of the
last anchor stays lost (no global recovery, by design).

## 4. Work packages (red first at each step)

Branch `codex/csrt-gray-s4` from `origin/main` in a clean worktree (the current checkout is the stale `codex/csrt-gray-s3`;
two unrelated untracked plan files there must not travel).

**P0 Preflight.** Fetch; confirm base `d8144a3` or newer; run `cargo test --locked -p openbar-tracking --all-targets --all-features`
and fmt; retain command/cwd/exit under `target/csrt-gray/s4/`. Reconcile any new overlapping work (stop condition).

**P1 Peak/PSR (`peak.rs`)** - tests on hand-built arrays: wrapped decoding both signs; search-domain edge (`side/4` accepted,
`side/4+1` ignored even when larger); half-period bin never selected; tie order independent of scan order; two spatially separate
equal maxima -> `Ambiguous`; equal values inside the exclusion square are not ambiguous; empty sidelobes and constant sidelobes ->
`FlatSidelobes`; PSR against a hand-computed value; non-finite response -> typed error. Then implement.

**P2 Config/identity/errors** - every field accepted/rejected at its boundary, threshold order, NaN/inf, iteration limits; identity
map is exactly the documented key set and byte-stable; `Display` for the three new variants. Rename the S3 struct (F3) and confirm
the S3 digest `0x4045_6d96_94a9_7f6a` and S1/S2 digests are unchanged.

**P3 State machine (`step`, unit tests)** - seed preserved exactly (centre, bounds, timestamp, frame_index); self-response
rejection paths; stationary; +x/-x/+y/-y shifts through the tracker (sign check composed with S2/S3); low-confidence freezes model
(assert `filter_fft` bit-equal) but moves anchor; loss freezes both; tracked frame with unavailable new-anchor crop skips the
update (F5); `bounds_fit` failure leaves state untouched; re-entry sets `reacquired_after_loss`; beyond-bound return stays lost.
Then implement the loop and `blend_filters` (tested: `learning_rate` 0 returns old, 1 returns candidate, Hermitian preserved,
mismatched lengths typed error).

**P4 Public tracker + shared-validation tests** - empty, reordered/non-increasing, dimension-changing, unmatched seed, one-frame
sequence, seed not first frame, seed on exact crop edge vs one pixel off, flat seed, radius > 127 (crop > 512), invalid config.
Remove the `expect(dead_code)` attributes that become consumed; clippy (`-D warnings`) reports unfulfilled expectations, so
remove exactly those and keep the rest.

**P5 Synthetic sequences (`tests/csrt.rs`)** - deterministic generator in test code (integer hash noise background, asymmetric
target such as a disk with an off-centre tab, no `rand` dependency; frames about 128x96 so a 32-px crop fits around motion).
Scenarios, with the expected outcome stated as a hypothesis to be observed, not evidence:

| Scenario | Assertion (invariants first) |
|----------|------------------------------|
| Stationary | all accepted, error <= 1 px, timestamps/frame indices preserved |
| Signed translations, each axis and diagonal, 1-2 px/frame | error <= 1 px per accepted coordinate; direction correct |
| Irregular timestamps, `frame_index None` mix | values passed through exactly |
| Global brightness gain/offset step | still tracked (normalisation invariance) or explicit loss; never a wrong coordinate |
| Partial occlusion | invariants: any non-lost observation within error bound; no coordinate on lost frames |
| Full occlusion then return at the same place | lost frames carry no coordinate; first return frame `reacquired_after_loss`; model unchanged across the gap |
| Distractor inside / beyond search bound | no persistent switch to the distractor, or explicit loss; count false tracks directly (F1) |
| Exit through an edge and return | `NoCandidate` loss at the edge; recovery only if back within `side/4` of last anchor |
| Motion beyond `side/4` per frame | explicit loss or counted false tracks; scenario kept, never softened |
| Fractional seed centre | pins the <= 0.5 px integer-anchor offset (F7) |
| Largest supported crop (radius ~100) | 3 frames complete without panic/overflow; measure debug-build time and record it |
| Determinism | two complete `TrackerRun` values `==` |

A scenario that fails the hypothesis is recorded and diagnosed (parent section 8); it is not edited into a pass.

**P6 Benchmark integration** - build `GroundTruthSample` for frames where the target is present, **excluding the seed
timestamp**, and call `evaluate_tracker_case(..., BenchmarkParameters{ timestamp_tolerance_s: 0.0, min_confidence: 0.0 })` on
`run.benchmark_predictions()` as `tests/trackers.rs` does. Assert availability and MAE from the evaluator; a separate exact
seed test covers the seed; false tracks are counted directly (F1). No new MAE/availability formulas.

**P7 Docs** - `TRACKER_EXPERIMENTS.md` candidate C: new S4/S5 contract (peak, PSR, thresholds, loss mapping, adaptation, identity,
limitations F4/F5/F7), observed synthetic results with config and commit, and the explicit "not selected, not evaluated on real
video" status; parent plan slice table and S4/S5 checkboxes; correct the S2 note about rewording the old variant (F2).

## 5. Verification and evidence

```bash
cargo fmt --all -- --check
cargo clippy --locked --workspace --all-targets --all-features -- -D warnings
cargo build --locked --workspace --all-targets --all-features
cargo test --locked -p openbar-tracking --all-targets --all-features
cargo test --locked --workspace --all-targets --all-features
cargo test --locked -p openbar-core analysis::tests::golden_json_is_stable
python -m unittest discover -v -s validation/tests -p 'test_*.py'
python validation/tools/schema_check.py
cargo deny --all-features --locked check
```

Plus the CLI smoke commands exactly as in `AGENTS.md` (`tracker-experiment`, `filter-experiment`, `tracker-run`, `analyze`,
`benchmark`) with `OPENBAR_REQUIRE_FFMPEG=1`. They matter here because the tracker becomes a public export of a crate the CLI
depends on; a skipped FFmpeg test is not a pass. Record any gate that could not run.

Retain under `target/csrt-gray/s4/` (git-ignored): base/head commits, exact commands, cwd, exit codes, test counts, scenario
results, repeated-run comparisons. Run S1/S2/S3 digests on the Windows and Ubuntu CI jobs; aarch64 stays unproven.

Reviews: one diff-first `measurement-audit` / `code-reviewer` pass on the finished diff, with the review focus from parent
section "Review focus" (conjugation/sign through the tracker, crop edges, flat/ambiguous freezing learning, private anchor never
exposed as a lost coordinate, resource caps). A validator or reviewer must reproduce the gates; do not accept a claimed pass.

## 6. Stop conditions

Stop and report if: a schema/version/golden/fixture or core-metric change turns out to be necessary; existing S1-S3 digests move;
template/contrast output changes; the sign or oracle checks fail; crop limits would require silent resizing or padding; a
dependency is needed; held-out or #79 data would be needed; newer main already overlaps this work. A fixed-support grayscale
tracker that fails declared scenarios is a result to report, not a reason to add HOG, segmentation or scale.

## 7. Delivery

One focused PR if the diff is reviewable, otherwise two (S4 tracker + unit tests, then S5 integration scenarios) under the single
completion criterion in #176. Conventional commits `feat(tracking): ...` / `test(tracking): ...`, body referencing #176 and #57 with
`Refs`, never `Closes #57`. Put `Closes #176` only in the final PR. Do not merge automatically.

## 8. Remaining gates after this plan

Real-video development evaluation against the external CSRT baseline, #57 held-out selection with preregistered gates, #80 phone
runtime, and any visibility-aware false-track metric in core. None is started or implied here.
