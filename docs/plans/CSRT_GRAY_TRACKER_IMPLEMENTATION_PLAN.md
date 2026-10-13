# Rust grayscale CSRT-class tracker implementation plan

> **For agentic workers:** Use `superpowers:executing-plans` when available to execute task-by-task. Keep tightly coupled implementation in one agent; obtain one diff-first independent review per completed slice. Track steps using the checkboxes below.

**Goal:** Continue the merged numeric foundation into an opt-in, decoder-independent, fixed-scale grayscale correlation tracker without waiting for new #79 recordings.

**Architecture:** Reuse `GrayscaleImage`, `FrameSample`, `ManualSeedTracker`, `TrackerRun` and core benchmarking. Preprocessing and appearance-filter learning live in `openbar-tracking::csrt`; calibration, trajectory filtering, kinematics and metrics remain in core. Ship reviewed slices before CLI integration or production selection.

**Tech stack:** Rust 2021, existing dependencies, generated in-memory grayscale fixtures, existing deterministic FFT/math.

**Spec:** This document defines the proposed experiment. Read alongside current AGENTS.md, ADR-0003/0005/0007/0008/0009, `docs/validation/TRACKER_EXPERIMENTS.md`, `docs/validation/TRACKER_FILTER_SELECTION.md` and `docs/plans/VBT_WORKFLOW_PLAN.md`.

Status: proposed handoff, 2026-10-13; see "Slice status" below for what has been implemented.
Related: #57, #80, #110; prerequisite PR #171; independent frozen study #79.

## Slice status

| Slice | Status |
|-------|--------|
| S0 preflight | done on base `1ee0558`; S1 digests and tracking tests green before S2 edits |
| S1 numeric core | merged (#171) |
| S2 patches and features | implemented on `codex/csrt-gray-s2`; internal only, awaiting review/publication |
| S3 filter learning | not started |
| S4 tracker state and loss | not started |
| S5 synthetic benchmark | not started |

S2 rulings (departures from the text below, all internal to `csrt::patch`):

- **Exact integer statistics.** Crop mean and population RMS are computed from exact integer sums
  of the 8-bit intensities rather than from `f64/255` values. The normalised value is
  scale-invariant, so the result equals the specified formula; the change removes rounding noise
  (a flat crop reported an RMS of 8.5e-14 instead of 0, and a crop with RMS of exactly one level
  could fall below the threshold). The threshold is one intensity level, i.e. `1/255` on the 0..1
  scale.
- **Error variants.** `NumericError` gained `InvalidRadius`, `RadiusTooLarge`, `RadiusExceedsPatch`,
  `UnsupportedPatchSide` and `InvalidSigma`. `Patch::InsufficientContrast` carries RMS in original
  intensity units.
- **Patch side limits** are enforced on the `Fft2d` passed to `normalized_patch` and
  `gaussian_label` (square, power of two, 8..=512), and on `support_mask`'s `side`, before any
  allocation sized from them. `support_mask` also requires `4 * radius + 1 <= side`, the same bound
  `patch_side` guarantees.
- **S4 note.** The existing `TrackerError::InsufficientSeedContrast` message names "the contrast
  tracker"; reword it before the CSRT tracker reuses the variant.

## 1. Baseline and current state

Inspected main: `1ee0558a9f189d72cdc31a33a1d4a4199dea669a`.
PR #171 delivered S1: `csrt/mod.rs`, `csrt/fft.rs`, `csrt/detmath.rs`.
It provides private numeric infrastructure, not a tracker or CLI integration.

The planning checkout is the old `codex/85-filter-window-s` worktree, which predates S1. Start implementation from current origin/main in a clean isolated worktree; carry only this plan, not old source. Re-read current instructions: main now uses MIT under ADR-0011; older worktrees describe the previous licence.

Verified interfaces on main:

- `Fft2d::try_new(width: usize, height: usize) -> Result<Self, NumericError>`; `forward(&self, input: &[Complex])` and `inverse(&self, input: &[Complex])` return `Result<Vec<Complex>, NumericError>`. Forward is unnormalized; inverse is normalized.
- `Complex::{new, conj, scale, norm_sqr, is_finite}` and ordinary arithmetic.
- `detmath::{hann_window_periodic, det_exp, MAX_TRANSFORM_LEN}`; maximum transform side 4096. That ceiling is not permission to allocate every maximum-size intermediate.
- `ManualSeedTracker::track(&self, frames: &[FrameSample<'_>], seed: &ManualTargetSeed) -> Result<TrackerRun, TrackerError>` and `identity()`.
- `TrackerRun::benchmark_predictions()` adapts observations to core benchmarking.
- Shared sequence/seed validation, `rounded_point`, `bounds_for_center`, `bounds_fit` and `lost_observation` live in tracking lib.rs. Inspect their exact signatures before calling; reuse their semantics.
- `TrackerObservationState` distinguishes tracked, low-confidence and lost. Existing diagnostics carry quality score, displacement and reacquisition.
- S1 tests register every CSRT source file in `SOURCES`. Register new files, preserve the scanner, and provide exactly one conventional `#[cfg(test)] mod tests` block per scanned file. Preserve pinned S1 digests.

## 2. Scope and completion

Build a **single-channel, fixed-support, fixed-scale experimental variant**, not an OpenCV port or full CSR-DCF implementation. Grayscale channel weighting is trivial. Adaptive segmentation, HOG, scale and rotation estimation are deferred.

Completion means the public tracker implements the existing trait and passes deterministic synthetic correctness, failure and benchmark integration tests. Being implemented in the tracking crate does not select it as a production tracker.

Keep #79's tracker, binary/commits, presets, parser, thresholds and inventory frozen. Do not use its new cohort or #57 held-out data for implementation or tuning. Existing declared development inputs belong to a separate evaluation follow-up.

No phone/Flutter/camera code, automatic detection, fallback tracker, dependencies, alternate persistence, calibration/kinematic changes, persistent fixture edits, existing version bumps or golden regeneration. CLI exposure and real-video runners are later work.

## Global constraints

- Preserve raw observations; lost samples carry no centre, bounds or synthetic confidence.
- Preserve timestamps/frame indices; never infer timing from nominal FPS.
- Display pixels: +X right, +Y down, integer pixel centres; preserve ADR-0007 bounds.
- Use S1 math; no platform transcendentals, FMA, parallel reductions or unordered accumulation. Never weaken the scanner to admit a helper.
- BTreeMap provenance; no random IDs or wall-clock values in measurement output.
- Validate floats, dimensions, products and indices before allocation/indexing. Numeric failure is typed, not a plausible coordinate.
- No new dependency or copied upstream implementation. Cite literature; independently written code follows current repository MIT licensing.
- Record effective experimental parameters; confidence is an algorithm-specific score, not a probability or accuracy verdict.

## Review focus

1. FFT conjugation/axis sign: positive image translation produces positive decoded displacement.
2. Crop edges: no clamping or replicated pixels that silently shift the target.
3. Flat/ambiguous response: emit loss and freeze learning.
4. Occlusion/re-entry: last accepted anchor is private state, never a lost observation coordinate.
5. Resource/platform limits: cap allocation; x86_64 digest agreement does not prove aarch64 behavior.

## 3. Proposed experiment design

### Geometry and features

Use a square integer-pixel crop without resizing/interpolation. For seed radius `r_px`, side is the next power of two of `ceil(4*r_px + 1)`, minimum **8**, maximum **512**. Validate finite radius and checked arithmetic before integer conversion/allocation. Reject unsupported seed crops; 512 is a prototype ceiling, not measured phone suitability.

At integer anchor `(cx,cy)`, crop `(i,j)` reads image `(cx+i-side/2, cy+j-side/2)`. Anchor lies at crop index `(side/2,side/2)`. Use existing `rounded_point` for fractional seed localization and document integer-pixel tracking. Preserve the exact authored centre in the seed observation.

An out-of-image crop is unavailable: no padding, reflection, image wrapping, clipping or whole-frame recovery. At seed it is an error; later it produces loss. This conservative edge limitation is intentional.

Convert intensity to `f64/255`. Compute mean/population RMS in row-major order; RMS below `1/255` is insufficient contrast. Subtract mean, divide by RMS, then multiply the separable S1 periodic Hann windows. No post-Hann renormalization.

Gaussian desired response peaks at **FFT index (0,0)**, not crop centre. Wrapped axis distance is `min(i,side-i)`. Evaluate `det_exp(-(dx*dx+dy*dy)/(2*sigma_px*sigma_px))` with initial `sigma_px=2`.

Use a fixed binary disk support of seed radius centred at `(side/2,side/2)` in filter coordinates. This deliberately omits the published method's adaptive spatial reliability.

### Correlation and constrained learning

Detection is `R = inverse(conj(H)*Z)`, taking validated real components. Prove its orientation against a direct spatial circular-correlation oracle before integration.

Define the simplified objective as `(1/N)*||conj(H)*X-Y||² + lambda*||g||²`, constrained to `H=FFT(g)` and `g=mask*g`; N is patch area. With scaled spatial dual u, start g/u at zero and use fixed-count ADMM:

1. `H = (X*conj(Y) + mu*FFT(g-u)) / (|X|²+mu)` elementwise.
2. `h = real(inverse(H))`.
3. `g = mask*(mu/(lambda+mu))*(h+u)`.
4. `u = u+h-g`; `mu = min(mu*mu_growth,mu_max)`.

Return `FFT(g)` so the effective filter obeys support. Reject nonfinite intermediates and imaginary residual above `1e-9*max(1,max_abs_real)`. Fixed row-major evaluation order. These equations define this independently derived simplified objective; no OpenCV parity claim is made.

Initial experimental values, frozen before real development evaluation:

| Parameter | Value |
|---|---:|
| lambda | 0.01 |
| admm_iterations | 4 |
| mu_initial / mu_growth / mu_max | 1 / 3 / 81 |
| learning_rate | 0.02 |
| label_sigma_px | 2 |
| lost_psr / tracked_psr | 5 / 8 |
| psr_exclusion_radius_px | 2 |
| seed_timestamp_tolerance_s | 0.0005 |

These are synthetic-development settings, not recommended VBT thresholds. Their efficacy is untested.

### Peak, score and state updates

Decode bin i as i when `i<side/2`, otherwise `i-side`. Exact half-period displacement is disallowed. Search only `abs(dx),abs(dy)<=side/4`. Highest response wins; ties prefer smaller squared displacement, then `(dy,dx)` lexicographically. Equal competing maxima outside the excluded peak neighbourhood are ambiguous and produce loss.

PSR is `(peak-sidelobe_mean)/sidelobe_population_std`, excluding the wrapped square of radius 2 around the selected peak. Compute statistics over the full response, in fixed order. Empty sidelobes, std <=1e-12, ambiguous maxima or nonfinite scores mean unusable response, not certainty. Record finite PSR in existing `quality_score`.

PSR below 5 is lost, [5,8) is low-confidence, >=8 is tracked. Confidence is `max(psr,0)/(max(psr,0)+tracked_psr)`, bounded f32; document its algorithm-specific meaning.

Validate accepted centres/bounds with existing helpers. Update the local anchor on tracked or low-confidence states. Update appearance only on tracked states: `H_next=(1-learning_rate)*H_old + learning_rate*H_candidate`. Never learn on low-confidence/lost frames. Local re-entry may recover within the retained search region; global reacquisition is not supported.

The seed observation preserves its authored centre/bounds and uses self-response confidence under the same score mapping. Reject an unusable seed or seed PSR below the loss threshold. Preserve original timestamp/frame_index. Do not map missing selection confidence to certainty.

## 4. Files and interfaces

| File | Responsibility |
|---|---|
| `crates/openbar-tracking/src/csrt/patch.rs` (new) | crop geometry, preprocessing, support, Gaussian |
| `crates/openbar-tracking/src/csrt/filter.rs` (new) | ADMM learning, response, model interpolation |
| `crates/openbar-tracking/src/csrt/tracker.rs` (new) | config, peak/PSR, sequential trait implementation |
| `crates/openbar-tracking/src/csrt/mod.rs` | declarations, internal errors, scan registration, exports |
| `crates/openbar-tracking/src/lib.rs` | public exports and typed numeric-error boundary |
| `crates/openbar-tracking/tests/csrt.rs` (new) | generated sequences and benchmark integration |
| `docs/validation/TRACKER_EXPERIMENTS.md` | method, parameters, limits, evidence status |

Proposed exact interfaces (private except tracker/config):

```rust
// patch.rs
pub(super) enum Patch {
    Available(Vec<Complex>),
    OutsideFrame,
    InsufficientContrast { rms: f64 },
}
pub(super) fn patch_side(radius_px: f64) -> Result<usize, NumericError>;
pub(super) fn normalized_patch(image: &dyn GrayscaleImage, anchor_px: (i32,i32), fft: &Fft2d) -> Result<Patch, NumericError>;
pub(super) fn gaussian_label(fft: &Fft2d, sigma_px: f64) -> Result<Vec<Complex>, NumericError>;
pub(super) fn support_mask(side: usize, radius_px: f64) -> Result<Vec<f64>, NumericError>;
// filter.rs; vectors have width*height elements
pub(super) fn train_filter(fft: &Fft2d, features: &[Complex], label: &[Complex], mask: &[f64], config: &CsrtGrayConfig) -> Result<Vec<Complex>, NumericError>;
pub(super) fn response(fft: &Fft2d, filter: &[Complex], features: &[Complex]) -> Result<Vec<f64>, NumericError>;
// tracker.rs
pub struct CsrtGrayConfig { /* typed fields from the parameter table */ }
pub struct CsrtGrayTracker { /* validated config */ }
impl CsrtGrayTracker {
    pub fn try_new(config: CsrtGrayConfig) -> Result<Self, TrackerError>;
}
impl ManualSeedTracker for CsrtGrayTracker { /* existing trait */ }
```

`normalized_patch` distinguishes ordinary unavailable outcomes from arithmetic failure. At seed, OutsideFrame maps to SeedTargetOutsideFrame and InsufficientContrast maps to the existing InsufficientSeedContrast with RMS expressed in original pixel-intensity units. Later frames map them to NoCandidate and InsufficientContrast loss respectively. NumericError aborts the run. Inspect enums before adding variants. If needed, add narrow `TrackerError::CsrtNumerical { reason: String }` plus Display/Error; never classify arithmetic failure as ordinary match loss. S2 returns spatial preprocessed values; S3 callers transform them once to spectra before train_filter/response.

Config derives Copy/Clone/Debug/PartialEq. Validate finite positive lambda/mu/sigma/tolerance; iterations 1..=64; growth >=1; max mu >=initial; learning rate [0,1]; `0<lost_psr<tracked_psr`; exclusion radius >=1 and below half the supported minimum crop. Geometry ceiling is fixed, not a tuning option. Record every effective parameter/constant in identity configuration using existing stable formatting patterns.

## 5. Ordered work packages

### S0 — Preflight

- [x] Fetch main, choose a clean isolated worktree, preserve unrelated edits. Suggested first branch: `codex/csrt-gray-s2`.
- [x] Read current instructions/contracts, this plan, S1 implementation/tests. Record base commit and intervening changes.
- [x] Run `cargo test --locked -p openbar-tracking --all-targets --all-features` and fmt check; retain command/cwd/exit evidence. (Logs are kept locally and git-ignored under `target/csrt-gray/s2/`; run with the S2 head, S1 tests and pinned digests green.)
- [ ] Read the primary paper; record single-channel/fixed-support deviations. Do not read upstream implementation code.
- [ ] Reconcile any newer overlapping tracker design before proceeding. Confirm proposed experiment design before S3; this document is not an accepted architecture decision.
- [ ] Use the literature references below as sources, not a copied implementation. The paper landing page was verified during planning; direct PDF retrieval was unavailable to the planning browser. Obtain the paper through its official arXiv mirror if necessary before the independent S3 mathematics review.

### S2 — Patches and features (first PR; initial handoff scope)

Consumes S1 math; produces the four patch.rs functions. No ADMM/config export/CLI.

- [x] Write failing `side_is_checked_and_power_of_two`: radius 24 ->128, radius 1 ->8; zero/negative/NaN/infinite/huge radius or side>512 errors before allocation.
- [x] Write `integer_centres_and_axis_orientation`: asymmetric intensity grid, exact crop indices, +X/right and +Y/down, odd frame dimensions and existing fractional-seed rounding.
- [x] Write `edge_crop_is_unavailable_without_padding` for all edges/exact fit; `flat_patch_is_unusable` for uniform/low RMS; `normalization_then_hann_is_fixed_order` against a hand-computed tiny patch, tolerance 1e-12.
- [x] Write `label_peaks_at_fft_origin`: label[0]==1, wrapped symmetry/finite entries, invalid sigma errors. `support_matches_seed_disk`: centre/rim/outside membership, invalid geometry errors.
- [x] Run `cargo test --locked -p openbar-tracking csrt::patch::`; observe red before implementation.
- [x] Implement only patch.rs, necessary internal error variants and declarations. Register the new source; preserve all S1 digests.
- [x] Run focused/all-tracking tests, fmt/clippy/build. Update experiment doc as S2-only infrastructure, obtain a diff-first coordinate/numeric review.
- [ ] When publishing is authorized, commit `feat(tracking): add deterministic grayscale CSRT patches`; PR references #57 without closing it. Stop at this deliverable.

### S3 — Constrained filter learning and response (second PR)

Consumes S2; produces train_filter/response. Define one config type for S3/S4, privately until S4; no duplicate config structs or unused public exports.

- [ ] Write `correlation_matches_spatial_oracle`: independent direct circular correlation on 8x8 inputs vs FFT response, tolerance 1e-9. Impulse translations left/right/up/down/mixed axes prove sign/conjugation.
- [ ] Write `full_mask_matches_ridge_reference`: small deterministic inputs, 64 iterations/constant mu, compare response with closed form `X*conj(Y)/(|X|²+lambda)` within 1e-6. Test-specific solver settings do not change experiment defaults.
- [ ] Write `masked_solution_matches_small_dense_reference`: 4x4 helper problem, independent circular design matrix and restricted ridge normal equations solved in test code, compare spatial coefficients within 1e-6 after 64 constant-mu iterations. Diagnose convergence/normalization failures; do not silently loosen tests.
- [ ] Add support-enforcement, repeated-bit-equality, wrong vector sizes, mask values other than 0/1, all-zero mask, nonfinite input and overflowing-intermediate tests.
- [ ] Observe red; implement section 3 equations, fixed-order validation and scan registration. Remove dead-code expectations only for consumed helpers.
- [ ] Run focused/all-tracking tests and gates; obtain independent mathematical review; document objective/normalization/deviations. Separate commit/PR; no selection/promotion.

### S4 — Tracker state, loss and conservative adaptation (third PR)

Consumes S2/S3; exports CsrtGrayConfig/CsrtGrayTracker implementing the existing trait.

- [ ] Config tests cover every field/range, threshold order, nonfinite values and iteration limits.
- [ ] Peak/PSR tests pin wrapped decoding, search domain, half-period rejection, deterministic tie ordering, spatially separate equal peaks, empty/flat sidelobes and score/confidence at thresholds. Use hand-built response arrays.
- [ ] Loop tests: exact seed preservation; stationary/signed translation; irregular timestamps; one-frame seed; empty/reordered/dimension-changing/unmatched-seed validation through shared helpers.
- [ ] State tests: occlusion emits no centre/bounds; low confidence freezes model; loss freezes model/anchor; local re-entry marks reacquisition; target beyond local search is not globally recovered. Assert private model state as well as outputs.
- [ ] Observe red; implement the policy. Sequence/config/numerical errors abort with typed error; ordinary quality failures emit loss. Bounds checks precede updates.
- [ ] New identity: id `csrt-gray-v1`, implementation `csrt-gray`, version `1`; explicitly experimental, grayscale/fixed-scale, algorithm-specific confidence. This is a new identity, not a canonical schema or existing tracker version bump.
- [ ] Run full workspace gates and independent measurement review; update docs; focused commit/PR. Preserve existing tracker identities/config/output.

### S5 — Synthetic benchmark integration (fold into S4 if small)

Consumes public tracker and benchmark adapter. No decoder/CLI/persisted fixture changes.

- [ ] Generate asymmetric targets/backgrounds in memory; known positions/timestamps are ground truth. Keep fixtures in new tests/csrt.rs, not media files.
- [ ] Stationary and slow signed translations: <=1 px per accepted coordinate, exact timestamp preservation, repeated whole TrackerRun equality.
- [ ] Brightness changes, partial/full occlusion, distractors, exits/re-entry, seed edges, resource ceilings, and motion beyond local-search bound. Expected unsupported cases explicitly lose; never weaken a failing scenario silently.
- [ ] Reuse core evaluator via benchmark_predictions and the integration pattern in tests/trackers.rs. Exclude seed from tracking-error reporting; keep a separate exact seed test. No duplicate MAE/availability/false-track formulas.
- [ ] Document measured synthetic results/config/commit/limitations. Expected numbers are assertions, not evidence until observed.
- [ ] Run gates and review. List remaining real-video and phone gates explicitly.

## 6. Verification and evidence

Each slice observes red first, then focused tests; all tracking tests before handoff. Source changes require fmt/clippy/build. Public exports/error enum changes require full workspace tests.

```text
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

Before public-trait handoff, run applicable tracker-experiment/filter-experiment/tracker-run/analyze/benchmark smoke commands exactly as listed in current AGENTS.md. Set OPENBAR_REQUIRE_FFMPEG=1 for full decode tests using the host shell's environment syntax. Record unavailable gates; a skip is not a pass.

Retain `target/csrt-gray/<slice>/` logs: baseline/head commits, exact commands, cwd, exit codes, test counts, numerical-oracle results and repeated-output comparisons. Run S1 digests on Windows/Ubuntu CI; add new digests only alongside independent oracle tests. Do not regenerate old digests to hide failure. aarch64 reproducibility remains unproven until tested there.

## 7. Subsequent decisions, not this implementation's acceptance

1. **Implementation:** synthetic correctness, invariants, review and experiment docs pass. This plan's completion gate.
2. **Development evaluation:** separately wire opt-in execution to existing decoder/prediction contracts, freeze config and compare declared development inputs with external OpenCV-CSRT baseline. Preserve default rosters/output; artifact-version implications require separate contract review. Retain actual runtime and false-track/availability results. No alternate measurement pipeline.
3. **#57 held-out selection:** freeze before accessing held-out annotations; assign original <3 px and >99% gates; no production selection from synthetic/development evidence.
4. **#80 phone:** frozen candidate, real reference-hardware benchmark and explicit M1 entry decision. No phone code in this plan.
5. **#79 study:** independent frozen method. Changing it requires a separate prospective amendment/new identity; this plan authorizes no source switch.

## 8. Stop conditions

Stop the affected slice and explain the conflict if dependencies, persisted schema/version, fixture/golden changes become necessary; S1 digests/platform results disagree; numeric or correlation oracles fail; resource limits require silent resizing/padding; invariants would be violated; frozen study/held-out inputs become necessary; or newer main already contains overlapping work.

If fixed-support grayscale fails declared synthetic cases, retain that result and diagnose before adding HOG, segmentation or scale estimation. Do not expand scope or report a stopped experiment as successful.

## 9. Sources, docs and rollout

Primary method: Lukežič et al., *Discriminative Correlation Filter with Channel and Spatial Reliability*, CVPR 2017:
[paper landing page](https://openaccess.thecvf.com/content_cvpr_2017/html/Lukezic_Discriminative_Correlation_Filter_CVPR_2017_paper.html),
[paper PDF](https://openaccess.thecvf.com/content_cvpr_2017/papers/Lukezic_Discriminative_Correlation_Filter_CVPR_2017_paper.pdf).

Use published mathematics; do not copy upstream implementation. Cite the paper in module docs and describe fixed support/single channel/scale omissions. Bundle no paper text, model or media. Independent code follows current repository MIT licensing.

Update this plan's slice status and tracker experiment contract in each slice. Reference #57; never close it with implementation-only PRs. Update ADRs only for actual architectural changes. Do not imply #79 outcomes or production selection in status docs.

## 10. Ready-to-send kickoff prompt

> Continue the Rust grayscale CSRT-class tracker in Szczepanov/openbar. Read docs/plans/CSRT_GRAY_TRACKER_IMPLEMENTATION_PLAN.md, current AGENTS.md and merged S1 from PR #171. Start from current origin/main in a clean isolated worktree, not codex/85-filter-window-s. Implement **S0 and S2 only**: checked crops, normalized grayscale/Hann features, Gaussian label, fixed disk support, deterministic tests and docs. Observe failing tests first; reuse S1; register every new CSRT source in the scan and preserve pinned digests. No ADMM/tracker loop, CLI, dependencies, persisted fixture/schema/version/golden changes, real study data or phone work. Run stated gates. Return focused diff, exact commands/cwd/exit codes, limitations and a proposed PR description referencing #57 without closing it. Publish only when authorized; never merge automatically. S3 is a separately reviewed next slice.

## 11. Planning self-review

Checked interface consumers and slice boundaries; FFT normalization/conjugation/origin/support conventions; score/update/loss policy; crop/resource edge tests; benchmark reuse; distinct new identity versus existing version changes; separated evidence/promotion/phone gates. No implementation tests or performance measurements were performed during planning. Initial feature/threshold efficacy remains untested.
