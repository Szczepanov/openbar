# VBT reference experiments — implementation plan

Status: active — Phases 1–3 executed; findings recorded in docs/analysis/VBT_REFERENCE_EXPERIMENTS_FINDINGS.md; Phases 4–5 deferred  
Related: #2, #57, #58, #53, ADR-0004, ADR-0008, `research/PLATE_TRACKING_PLAN.md`, `docs/analysis/VBT_REFERENCE_EXPERIMENTS_FINDINGS.md`  
Research basis: `docs/analysis/VBT_OPEN_SOURCE_REFERENCE_REVIEW.md`

## 1. Purpose

Convert the VBT/CV reference review into an executable sequence of experiments without turning
interesting external techniques into automatic OpenBar requirements.

This plan covers five follow-up areas:

1. image-coordinate transform correctness;
2. zero-phase Butterworth filtering as a challenger family;
3. multi-frame plate-size calibration robustness;
4. camera-geometry sensitivity (camera perpendicularity, plate-plane depth, video stabilization and
   lens distortion);
5. controlled marker/fiducial tracking as an optional reference aid.

The plan deliberately separates:

```text
implement experiment
        |
        v
collect evidence
        |
        v
make explicit decision
        |
   +----+----+
   |         |
 reject    promote
             |
             v
   separate production/contract work
```

A successful experiment does not automatically justify changing production measurement behavior.

## 2. Execution status & phase dashboard

Execution commenced per §6 and §21. Detailed empirical findings, distributions, and reproduction commands are recorded in [`docs/analysis/VBT_REFERENCE_EXPERIMENTS_FINDINGS.md`](docs/analysis/VBT_REFERENCE_EXPERIMENTS_FINDINGS.md).

| Phase / Work Package | Focus | Status | Implementation / Evidence Reference | Explicit Decision |
|---|---|---|---|---|
| **Phase 1** | Display coordinate invariant | **COMPLETED** | [`docs/validation/TRACKER_EXPERIMENTS.md`](docs/validation/TRACKER_EXPERIMENTS.md) | **DOCUMENTED (DOCS ONLY)** |
| **Phase 2 (P2.0)** | Real-timestamp applicability survey | **COMPLETED** | [`research/vbt-experiments/timestamp_survey.py`](../../research/vbt-experiments/timestamp_survey.py), `target/research/butterworth/timestamp_survey_dev.json` | **GO** (1% low-jitter rule qualifies 80% dev, 83.3% val clips) |
| **Phase 2 (P2.1–P2.4)** | Butterworth challenger implementation & grid | **COMPLETED** | [`crates/openbar-core/src/filtering.rs`](../../crates/openbar-core/src/filtering.rs), [`apps/openbar-cli/src/filter_experiment.rs`](../../apps/openbar-cli/src/filter_experiment.rs) | **CONTINUE RESEARCH / DEFER PROMOTION** (Savitzky-Golay superior on peak velocity attenuation; promotion deferred to #58) |
| **Phase 3 (P3.0)** | Multi-frame calibration oracle study | **COMPLETED** | [`research/vbt-experiments/calibration_oracle_study.py`](../../research/vbt-experiments/calibration_oracle_study.py), `target/research/calibration/p3_annotation_oracle_study.json` | **REJECT ESTIMATOR PROMOTION / KEEP DIAGNOSTIC** (blur causes 3–12% plate shrinkage; S0 remains authoritative) |
| **Phase 4** | Camera-geometry sensitivity | **DEFERRED** | Section 11 (stub retained) | **DEFERRED** (trigger conditions in §11 not met) |
| **Phase 5** | Controlled marker reference | **DEFERRED** | Section 12 (stub retained) | **DEFERRED** (trigger conditions in §12 not met) |

## 3. Current state at plan authoring

At the time this plan was written:

- ADR-0008 records **NO-GO / REWORK** for M1 entry.
- #57 owns held-out tracker/filter selection.
- #58 owns independent physical/reference validation of ROM and velocity.
- #53 owns the evidence-backed supported recording envelope.
- the existing production-authoritative filter families are:
  - raw identity;
  - centred moving average;
  - timestamp-aware Savitzky-Golay;
  - constant-velocity Kalman;
- `PlateDiameterCalibration@1` uses one explicit manual seed/reference diameter;
- timestamps, not nominal FPS, are authoritative;
- raw observations are preserved and tracking loss is explicit;
- the repository already contains a separate OpenCV tracking research plan and spike under
  `research/PLATE_TRACKING_PLAN.md` and `research/opencv-tracking/`.

This plan does **not** replace the tracker plan. It layers additional experiments around the current
M0 re-entry work.

## 3. Goals

The plan should answer five concrete questions.

### G1 — Coordinate transforms

Can every future crop/resize/rotation/letterbox preprocessing path prove that tracker/model
coordinates map exactly back to canonical OpenBar display/source pixel coordinates before
calibration?

### G2 — Butterworth

On timestamp sequences where regular-sampling assumptions are actually satisfied, does a
zero-phase Butterworth family improve held-out position/velocity/peak behavior enough to justify
promotion beyond the current four filter families?

### G3 — Multi-frame calibration

Does robust multi-frame visible plate-diameter estimation improve independently referenced physical
position/ROM accuracy over the existing explicit single-seed calibration, or is visible-diameter
variation more useful as a geometry-quality diagnostic?

### G4 — Camera geometry

Which camera-geometry error sources — camera yaw/tilt away from perpendicular, depth mismatch between
the plate and the calibrated plane, electronic video stabilization, and lens distortion — materially
harm measurements inside the intended side-view recording envelope? Is any of them better handled by
correction than by a tighter recording envelope?

### G5 — Controlled marker reference

Can a deliberately distinctive marker/fiducial provide a sufficiently accurate secondary reference
trajectory to reduce manual annotation effort without being confused with a production tracking
solution?

## 4. Non-goals

This plan does not authorize:

- Go or GoCV in the OpenBar runtime;
- DeepLabCut in the runtime;
- third-party model/checkpoint reuse from unlicensed repositories;
- an OpenCV production dependency;
- a new default filter;
- replacing `PlateDiameterCalibration@1`;
- automatic rep detection;
- live camera mode;
- pose estimation;
- Flutter work;
- schema/version changes merely to make research easier;
- weakening timestamp, confidence, loss or raw-preservation invariants.

If an experiment later requires a persisted-contract or architecture change, stop and create the
normal ADR/schema/version review first.

## 5. Governing constraints

Every work package must preserve these repository rules.

### Measurement

- raw observations are never overwritten;
- a lost observation has no fabricated coordinate;
- authoritative timestamps drive time-dependent calculations;
- nominal FPS is metadata, not derivative truth;
- confidence is algorithm-specific and not silently converted into certainty;
- no long gap is silently interpolated;
- same input + implementation/version/configuration produces deterministic retained output.

### Validation

- development/tuning evidence is separate from held-out evidence;
- do not inspect held-out results before freezing parameters;
- physical/reference comparisons must be construct- and interval-matched;
- missing/extra observations are reported rather than silently removed;
- comparison conclusions are bounded to represented devices, exercises, geometry and conditions.

### Clean room / licensing

- public visibility is not permission to copy;
- external source, exact version/commit and licence must be recorded;
- unlicensed `app_vbt` and `BarTracker_app_vbt_validation` remain reference-only;
- framework licences do not imply model/dataset redistribution rights;
- no external code or model artifact enters production merely because a research experiment used an
  independently licensed tool.

## 6. Priority and dependency graph

The recommended sequence is:

```text
                         current M0 baseline
                                |
             +------------------+------------------+
             |                                     |
             v                                     v
  P1 transform correctness              #57 tracker evidence plan
             |                                     |
             |                                     v
             |                         frozen tracker candidate(s)
             |                                     |
             +------------------+------------------+
                                |
                                v
                       #58 physical reference
                                |
              +-----------------+-----------------+
              |                 |                 |
              v                 v                 v
       P2 Butterworth    P3 multi-frame      P4 camera
          challenger       calibration         geometry
              |                 |                 |
              +-----------------+-----------------+
                                |
                                v
                       explicit decisions
                                |
                    promote only if justified

P5 controlled marker reference
    -> optional side path, implement only if annotation/reference acquisition is a bottleneck
```

Important sequencing rule:

> P2–P4 must not automatically delay #57/#58. They are challengers/follow-ups. Promote them onto the
> critical path only when existing evidence exposes a problem they are likely to solve.

Work that does **not** wait for #58 and can run from the current repository state:

- P1 invariant documentation (no code);
- P2.0 real-timestamp applicability survey (go/no-go for all further P2 work);
- P2.2–P2.4 synthetic implementation and tests, only if P2.0 passes;
- P3.0 annotation-oracle study, if annotated per-frame plate diameters exist.

## 7. Phase 0 — freeze and record the baseline

### Objective

Create a reproducible baseline against which each follow-up experiment is evaluated.

### Work package P0.1 — baseline inventory

Record in the experiment plan/report:

- Git commit;
- current tracker candidates/frozen candidate state from #57;
- current selected-per-family filter configurations from `filter-experiment`;
- current plate calibration method/version;
- kinematics method/version;
- relevant fixture manifest hash;
- current #58 reference-study contract;
- current recording-envelope state.

Do not create a second competing source of truth if #57/#58 already persist these values. Link or
reuse their artifacts.

### Work package P0.2 — baseline outputs

For any real/reference cases used by P2–P4, retain baseline outputs from the unchanged pipeline:

- canonical analysis JSON;
- independent reference input;
- tracker/filter provenance;
- calibration provenance;
- ROM/mean/peak error;
- relevant condition labels.

### Acceptance

- baseline can be reproduced from recorded commit/configuration;
- no new experiment has altered production defaults;
- held-out material remains unexposed before its freeze point.

### Stop condition

If #57/#58 do not yet provide sufficient real/reference material, do not invent a substitute and do
not make P2–P4 blockers. Continue the existing M0 re-entry work first.

---

# 8. Phase 1 — coordinate-transform correctness

Priority: **low now (document the invariant); high as soon as a transformed-frame consumer appears**  
M0 blocker: **no**, unless a current research/production tracker performs transformed-frame
inference and returns transformed coordinates.  
Status: **COMPLETED (DOCS ONLY)** — invariant recorded in [`docs/validation/TRACKER_EXPERIMENTS.md`](docs/validation/TRACKER_EXPERIMENTS.md).

## Rationale

A fixed resize can change X/Y scale differently. Any future model pipeline may crop, resize,
letterbox or rotate input for inference. Calibration is valid only when observations are mapped back
into the canonical coordinate space.

The current repository search found no general crop/resize/letterbox coordinate-transform
abstraction. Do not add a broad abstraction unless an actual consumer needs it; implement the
smallest reusable representation required by the first transformed-frame path.

## Current state: stop condition already met

At the time of writing no measurement path emits transformed coordinates:

- the Rust `template` and `contrast` trackers operate on full decoded frames;
- the OpenCV research trackers (CSRT, KCF, ViTTrack, NanoTrack, DaSiamRPN) resize internally for
  inference but return boxes in the coordinates of the frame they were given;
- `cv2.resize` in `research/opencv-tracking/track.py` only rescales a template patch for a QA
  similarity score and does not produce measurement coordinates;
- display rotation, the one transform that does exist, is already handled: the FFmpeg decode
  boundary records `rotation_deg` and emits display-oriented frames (`apps/openbar-cli/src/media/`,
  ADR-0006), and the Python tooling uses `display_size` in `validation/tools/label_package.py`.

Immediate action is therefore **documentation only**: record the invariant

> All tracker observations, seeds, annotations and bounds are in decoded display-oriented pixel
> coordinates after rotation, per ADR-0007. A tracker that runs on a transformed frame must
> map its output back before emitting observations.

in `docs/validation/TRACKER_EXPERIMENTS.md`. P1.1–P1.3 below apply only once a
consumer that crops, resizes or letterboxes before producing coordinates is introduced. Rotation in
P1.1 means reusing the existing decode-boundary rotation, not reimplementing it.

## P1.1 — define the coordinate-transform contract

Document or implement a transform representation capable of expressing, when needed:

- crop origin;
- source and destination dimensions;
- independent X/Y scale;
- padding/letterbox offsets;
- 0/90/180/270 display rotation where relevant.

Required operations:

```text
canonical/source point -> processed point
processed point        -> canonical/source point
```

Round-trip must be defined for floating-point pixel-centre coordinates.

### Candidate location

If/when used by Rust production/research code:

- `crates/openbar-core/src/` only if it is a genuine measurement primitive independent of OpenCV;
- otherwise keep the first implementation in the consuming research adapter and promote later.

Do not add OpenCV matrix/image types to `openbar-core`.

## P1.2 — deterministic transform tests

Add synthetic cases for:

1. identity;
2. uniform resize;
3. non-uniform resize;
4. crop + resize;
5. letterbox/padding;
6. 90/180/270 rotation if the chosen representation owns rotation;
7. inverse round-trip;
8. plate diameter/bounds mapping;
9. source boundary points and sub-pixel centres.

A key regression test must demonstrate that:

```text
non-uniform processed coordinates
+ one scalar metres-per-pixel in processed space
!= valid physical geometry
```

while inverse-mapping to canonical coordinates before calibration reproduces the source-space result.

## P1.3 — integrate only with an actual preprocessing consumer

When a research tracker/model introduces preprocessing:

- persist/log the exact transform;
- map target centres/bounds back before benchmark/calibration;
- keep tracker prediction output in canonical coordinates;
- compare transformed vs direct-source fixtures where possible.

## Acceptance

- point/bounds round trips are deterministic within a documented floating-point tolerance;
- a synthetic non-uniform-resize regression cannot silently pass scalar calibration in processed
  coordinates;
- no source/processed coordinate ambiguity exists in tracker prediction artifacts.

## Promotion gate

Promote a reusable core transform type only after at least one real production/research consumer
needs it. Until then, contract/tests may remain local to the adapter.

## Stop condition

If no current path resizes/crops/letterboxes before producing measurement coordinates, document the
invariant and defer implementation rather than creating unused framework code. This is the current
state (see above).

---

# 9. Phase 2 — Butterworth challenger experiment

Priority: **medium/high after baseline reference data exists**  
M0 blocker: **no by default**  
Status: **CONTINUE RESEARCH / DEFER PROMOTION** — P2.0–P2.4 executed; findings in [`docs/analysis/VBT_REFERENCE_EXPERIMENTS_FINDINGS.md`](docs/analysis/VBT_REFERENCE_EXPERIMENTS_FINDINGS.md).

## Rationale

A reviewed public VBT project uses a fourth-order zero-phase Butterworth low-pass filter with an
8 Hz cutoff. That is sufficient to justify evaluation, not adoption.

Unlike the existing timestamp-aware Savitzky-Golay and Kalman candidates, a conventional digital
Butterworth implementation assumes regular sample spacing. OpenBar must explicitly test that
assumption rather than substituting nominal FPS.

Phone video is commonly variable-frame-rate. If the applicability rule is strict, Butterworth may
apply to almost no real input, in which case the filter is not worth implementing. That question is
cheap to answer and needs no #58 reference data, so it gates everything else in this phase.

## P2.0 — real-timestamp applicability survey (go/no-go)

Status: **COMPLETED — DECISION: GO**.  
Executed via [`research/vbt-experiments/timestamp_survey.py`](../../research/vbt-experiments/timestamp_survey.py); artifact in `target/research/butterworth/timestamp_survey_dev.json`. Findings: strict tick-exact regularity fails real phone clips due to ~0.1 ms sensor clock jitter; frozen 1% low-jitter rule qualifies 80.0% dev and 83.3% validation clips while rejecting VFR/dropped frames.

Before implementing any filter:

1. implement the P2.1 regularity metric (a small, pure function; no filter code yet);
2. run it over the authoritative decoded frame timestamps of every available real development clip
   (public fixtures and private development material decoded through the ADR-0006 FFmpeg boundary,
   e.g. via `tracker-run`), per contiguous tracked segment;
3. report the distribution of the metric per device/recording mode, and the fraction of clips and
   of tracked segments that would be applicable under the strict rule (exactly regular after
   container time-base rounding) and under any candidate low-jitter rule;
4. freeze the applicability rule from development clips only; held-out clips are classified only
   after the freeze.

Decision:

- if the applicable fraction of realistic intended inputs is negligible under any defensible rule,
  record **REJECT** (or **DEFER** pending different capture settings) and stop P2 here;
- otherwise continue to P2.1–P2.5 with the frozen rule.

Container time-base rounding can make constant-rate video look slightly irregular. Account for it
explicitly in the metric (e.g. compare deltas in time-base ticks) rather than loosening the
threshold.

## P2.1 — define timestamp regularity

Status: **COMPLETED** — implemented `assess_timestamp_regularity` and `TimestampRegularity` in [`crates/openbar-core/src/filtering.rs`](../../crates/openbar-core/src/filtering.rs). Evaluates median dt, min/max dt, max relative deviation, and coefficient of variation with fail-closed validation.

Add a deterministic applicability calculation over each contiguous segment.

For timestamp deltas `dt_i`, record at least:

- sample count;
- median/mean `dt`;
- minimum/maximum `dt`;
- maximum absolute deviation from median;
- maximum relative deviation from median;
- coefficient of variation or an equivalent deterministic jitter metric.

Do **not** choose a production threshold from intuition.

Use three classes during development:

- exactly regular synthetic sequences;
- controlled low-jitter sequences;
- deliberately irregular/VFR-style sequences already represented in `filter-experiment`.

The first experiment may declare Butterworth unsupported for all non-regular sequences rather than
inventing a permissive threshold.

## P2.2 — implement experimental zero-phase filter

Status: **COMPLETED** — implemented `ButterworthExperimentalConfig` and `apply_butterworth_filter` in [`crates/openbar-core/src/filtering.rs`](../../crates/openbar-core/src/filtering.rs). Cascaded biquad direct form II, bilinear transform with pre-warping, forward-backward zero-phase application, Winter cutoff correction, and reflected endpoint padding.

Recommended approach:

- implement the algorithm independently in Rust from published DSP equations/reference literature;
- no SciPy runtime dependency;
- no nominal-FPS input;
- derive the sampling interval only after timestamp regularity is accepted;
- run forward/backward filtering for zero phase;
- segment on `max_gap_s`;
- do not synthesize missing samples;
- preserve each original timestamp exactly;
- define short-segment/padding/edge behavior explicitly;
- reject invalid normalized cutoff values.

### Order and cutoff definition

"Fourth-order zero-phase Butterworth" is ambiguous and must be pinned before the grid is run:

- **design order** — the order of the single-pass digital filter that is designed;
- **effective order** — forward/backward application doubles it (a 2nd-order design applied
  forward and backward has 4th-order effective magnitude roll-off; a 4th-order design has 8th);
- **cutoff correction** — double-pass application squares the magnitude response, so the −3 dB
  point of the combined filter falls below the single-pass design cutoff. Either design with the
  standard double-pass correction (Winter, *Biomechanics and Motor Control of Human Movement*,
  low-pass filter cutoff correction for multiple passes) so the stated cutoff is the effective
  −3 dB cutoff, or state that the cutoff is the uncorrected single-pass value.

Provenance must record design order, number of passes, effective order, the cutoff convention used
and the sampling interval derived from accepted timestamps. When comparing with the reviewed
external project, first establish which convention its "fourth-order, 8 Hz" refers to; if that
cannot be established, include both interpretations in the grid rather than guessing.

### Integration strategy

Avoid exposing Butterworth through canonical `analyze --filter` initially.

Preferred first implementation:

- add an experimental helper in `openbar-core::filtering` only if required to reuse the existing
  benchmark logic cleanly;
- wire it to `filter-experiment` as a research challenger;
- do not change canonical persisted analysis/filter CLI choices until the promotion gate passes.

If adding it to `FilterConfig` would automatically expand production/persisted semantics, use a
separate experiment-only config/function rather than changing the public enum prematurely.

## P2.3 — development grid

Status: **COMPLETED** — 20-candidate grid evaluated via `apps/openbar-cli/src/filter_experiment.rs` (`--challenger butterworth`); selected winner: `zero-phase-butterworth-research@1` (effective order 8, design order 4, 4.0 Hz cutoff, uncorrected single pass, dev vel RMSE 0.0384 m/s).

Predeclare a small grid before evaluating held-out scenarios.

Suggested initial grid:

- effective order: 4 (2nd-order design, forward/backward), plus effective order 8 only if the
  external reference turns out to mean a 4th-order design (see "Order and cutoff definition");
- effective cutoff: 4, 6, 8, 10, 12 Hz where valid for the measured sampling frequency;
- `max_gap_s`: keep aligned with the existing experiment unless evidence requires a separate
  development grid.

Do not add more degrees of freedom until the first grid shows a reason.

Selection rule should remain consistent with existing family selection:

1. minimize mean development velocity RMSE;
2. use mean position RMSE as deterministic tie-break;
3. never use held-out data for tuning.

## P2.4 — synthetic applicability/behavior tests

Status: **COMPLETED** — deterministic test suite implemented in [`crates/openbar-core/src/filtering.rs`](../../crates/openbar-core/src/filtering.rs) (covering 30/60/120/240 Hz regularity, Nyquist rejection, invalid cutoffs, gaps, VFR rejection, zero phase delay, constant signal preservation, determinism).

Required tests:

- exactly regular 30/60/120/240 Hz;
- cutoff at/above Nyquist rejected;
- non-finite/zero/negative cutoff rejected;
- one-sample and too-short segments handled explicitly;
- long gap resets/segments correctly;
- short missing span does not create samples;
- low jitter classified according to frozen rule;
- VFR scenario is rejected/unsupported if outside rule;
- forward/backward output has no timestamp shift;
- deterministic repeat run;
- constant input remains constant within tolerance;
- sharp-peak attenuation and edge transient behavior are measured.

## P2.5 — real/reference evaluation

Status: **EVALUATED — DECISION: CONTINUE RESEARCH / DEFER PROMOTION**.  
Evaluated against development synthetic scenarios and phone captures (see [`docs/analysis/VBT_REFERENCE_EXPERIMENTS_FINDINGS.md`](docs/analysis/VBT_REFERENCE_EXPERIMENTS_FINDINGS.md)). Findings: while Butterworth achieves zero phase delay and low vel RMSE on long loss spans (0.0312 m/s), Savitzky-Golay outperforms it on sharp peaks (0.5% vs 29.6% attenuation) and handles irregular/gap timestamps natively. Production promotion deferred until #58 physical reference evidence is acquired.

Only after #58 provides suitable definition-matched reference data:

- use the same tracker/calibration/reference cases as existing filter candidates;
- freeze Butterworth development parameters;
- evaluate once on held-out/reference cases;
- compare:
  - position MAE/RMSE;
  - ROM error;
  - mean velocity error;
  - peak velocity error;
  - peak attenuation;
  - peak timing shift;
  - edge behavior;
  - applicability/rejection rate;
  - runtime.

Report unsupported timestamp cases rather than excluding them silently.

## Promotion gate

Status: **STOP CONDITION MET FOR PROMOTION — RETAIN AS RESEARCH CHALLENGER**.  
Evidence does not justify replacing or adding Butterworth to canonical production defaults without physical reference evidence (#58). Existing 4 production filter families remain authoritative.

Butterworth is eligible for production-candidate discussion only if:

- it applies to a useful fraction of intended phone-video inputs under a documented rule;
- held-out physical/reference error is materially better on at least one important dimension;
- it does not create unacceptable peak/edge artifacts;
- its improvement remains after condition stratification;
- complexity is justified versus timestamp-aware Savitzky-Golay/Kalman.

"Materially better" must be decided from #58 error distributions/reference uncertainty, not a
pre-invented percentage.

## Stop conditions

Stop and retain the existing four families if any is true:

- Butterworth is not applicable to realistic timestamp distributions (decided at P2.0, before any
  filter implementation);
- held-out improvement is negligible relative to reference/annotation uncertainty;
- peak/edge behavior is worse without a compensating benefit;
- a current family performs equivalently with fewer assumptions;
- using Butterworth would require silent resampling or nominal-FPS assumptions.

---

# 10. Phase 3 — multi-frame calibration robustness

Priority: **medium after tracker quality is adequate**  
M0 blocker: **no by default**  
Status: **COMPLETED — DECISION: REJECT ESTIMATOR PROMOTION / KEEP DIAGNOSTIC** (P3.0 oracle study findings in [`docs/analysis/VBT_REFERENCE_EXPERIMENTS_FINDINGS.md`](docs/analysis/VBT_REFERENCE_EXPERIMENTS_FINDINGS.md)).

## Rationale

`app_vbt` estimates visible plate size across frames and uses a median diameter. OpenBar currently
uses one explicit human-selected reference diameter, which has strong provenance and avoids tracker
circularity.

The experiment should test two separate ideas:

1. multi-frame diameter as a calibration estimator;
2. visible-diameter variation as a geometry/tracker quality signal.

The second may be useful even if the first is rejected.

## Prerequisite

A tracker/reference process capable of producing per-frame plate-size evidence is required.

**No current production tracker measures plate size.** Both Rust trackers emit `target_bounds_px`
built from the fixed seed radius around the tracked centre (`bounds_for_center(center,
seed.target().radius_px())` in `crates/openbar-tracking/src/template.rs` and `contrast.rs`). Their
bounds therefore always have the seed's size, and S1 over them would trivially reproduce S0.
Tracker bounds must not be used as diameter evidence.

The OpenCV research trackers do emit variable `(w, h)` boxes, but box size is a tracker-specific
quantity (search-window/regression behaviour), not a measured plate diameter. Use it only after it
has been validated against annotated diameters, and never as the only evidence.

Do not add a new ML model or a size-measuring tracker just to run this study. If a size-measuring
tracker is needed, it is tracker work owned by #57 and its candidate gates, not part of P3.

## P3.0 — annotation-oracle study (go/no-go)

Status: **COMPLETED — DECISION: REJECT ESTIMATOR PROMOTION**.  
Executed via [`research/vbt-experiments/calibration_oracle_study.py`](../../research/vbt-experiments/calibration_oracle_study.py); artifact in `target/research/calibration/p3_annotation_oracle_study.json`. Findings: human annotators dynamically adjust aiming rings during barbell motion blur to fit high-contrast cores, resulting in 3.2% to 12.5% diameter shrinkage relative to the stationary seed ($S_0$). Multi-frame median diameter ($S_1$) artificially inflates velocity and ROM. Stop condition met: P3.1–P3.4 stopped. `PlateDiameterCalibration@1` remains authoritative; visible diameter variation retained strictly as P3.5 diagnostic signal for blur/geometry tracking quality (#53).

Before building any estimator harness, test the best case: perfect, human-measured per-frame
diameters.

- Source: the optional per-sample `target_size` (`radius_px`, `diameter_px` or box) in
  `annotation-v1` (`validation/schema/annotation-v1.schema.json`, `docs/validation/ANNOTATION.md`).
- Caveat: the annotation tool carries the aiming-ring radius forward from the previous frame, so a
  recorded radius is not necessarily an independent per-frame measurement. Use only frames whose
  radius was deliberately fitted, or annotate a small dedicated development subset with every
  radius fitted; record which convention was used.
- Compute S1/S2 from those diameters and compare them with S0 using the scale-ratio method in P3.4.

Decision:

- if even oracle per-frame diameters do not improve physical error over the single seed by more
  than reference uncertainty, record **REJECT** for the calibration estimator and keep only the
  P3.5 diagnostic question open;
- if they do, P3 continues only once a validated source of per-frame size exists (see above).

## P3.1 — define candidate estimators

Control:

- **S0:** current manual seed diameter.

Candidates:

- **S1:** median of eligible frame diameters;
- **S2:** trimmed/robust median-like estimate if outliers justify it;
- **S3:** confidence-weighted robust estimate only if confidence weighting has a defensible meaning.

Do not implement a continuously changing frame-by-frame metres-per-pixel scale in this study.

## P3.2 — eligibility rules

A frame diameter may enter the estimate only under predeclared conditions, for example:

- tracker state is tracked;
- confidence meets a development-selected threshold;
- bounds/diameter are finite and positive;
- target is fully within frame;
- optional geometry quality flags permit use.

Do not tune these conditions on held-out physical-reference results.

## P3.3 — diagnostics

For each case record:

- seed diameter;
- eligible diameter count / total;
- median;
- p05/p95 or robust spread;
- coefficient/relative spread;
- trend versus timestamp/vertical position;
- tracker confidence;
- known condition tags;
- calibration result under S0/S1/S2/S3.

Do not overwrite canonical analysis with research estimates.

## P3.4 — independent-reference comparison

Every estimator in this study produces one constant metres-per-pixel scale per case. Changing the
scale multiplies every calibrated position, ROM, mean velocity and peak velocity by the same ratio
`k = scale_candidate / scale_reference`. The primary comparison is therefore one number per case:

- **relative scale error** `k − 1` for each estimator, where `scale_reference` comes from the
  independent physical reference (a known rig distance, or reference ROM divided by the pixel
  displacement over the same matched interval).

Full-pipeline metric runs are not needed to rank estimators; ROM/velocity error contributed by
calibration follows directly from `k`. Report full-pipeline errors only for the final shortlisted
estimator, to confirm that nothing outside the scale changed.

Stratify `k − 1` by:

- plate position in frame;
- fast movement/blur;
- foreshortening/depth change.

Use the same raw tracker observations for every calibration estimator so the study isolates
calibration effects.

## P3.5 — geometry-quality hypothesis

Independently test whether diameter variation predicts unsupported geometry/tracking error.

Candidate descriptive signals:

- robust diameter spread;
- monotonic diameter change with vertical position;
- sudden diameter jumps;
- disagreement between tracker centre error and scale stability.

Evaluate false positives/negatives against known conditions/reference error.

Do not add a warning threshold to production until it demonstrates useful discrimination.

## Promotion gates

### New calibration estimator

A multi-frame method is eligible for promotion only if:

- it improves independent physical error beyond the baseline in representative cases;
- improvement is not merely caused by using tracker errors twice;
- behavior under occlusion/blur/foreshortening is understood;
- enough eligible frames exist reliably;
- a new calibration method/version can be specified reproducibly.

Promotion requires a separate calibration method/version and persistence review.

### Geometry-quality diagnostic

A diameter-variation diagnostic may be promoted independently if:

- it detects meaningful geometry/tracker failures with acceptable false-positive cost;
- threshold/semantics are evidence-backed;
- it does not pretend to identify the physical cause when multiple causes are possible.

## Stop conditions

Status: **STOP CONDITION MET — `PlateDiameterCalibration@1` RETAINED UNCHANGED**.  
Oracle study demonstrated that multi-frame diameter averaging without blur deconvolution degrades accuracy. Estimator promotion rejected; diameter variation retained strictly for diagnostic analysis (#53).

Keep `PlateDiameterCalibration@1` unchanged if:

- S1/S2/S3 do not materially improve physical error;
- results are dominated by tracker/bounds noise;
- improvement disappears on held-out cases;
- the method requires silent per-frame rescaling;
- added complexity exceeds measurable benefit.

Even if calibration promotion stops, retain useful evidence about diameter variation for #53 if
validated.

---

# 11. Phase 4 — camera-geometry sensitivity

Priority: **medium/low until recording-envelope evidence indicates need**  
M0 blocker: **no by default**  
Detail level: **stub** — expand into full work packages only when the trigger below fires.  
Status: **DEFERRED** (trigger conditions in §11 not met; stub retained).

## Trigger

Start P4 when #53/#58 evidence shows position/ROM error that varies with camera setup or field
position beyond reference uncertainty, or when #53 needs evidence to set framing/setup limits.

## Rationale

`PlateDiameterCalibration@1` assumes the plate moves in a plane parallel to the image plane at the
depth where the seed diameter was measured. Several error sources violate that assumption. Lens
distortion is only one of them, and on the main lens of current phones it is often already corrected
in the camera pipeline. The likely larger sources are:

- **G4a — camera yaw/tilt**: camera not perpendicular to the plane of bar motion (perspective);
- **G4b — plate-plane depth mismatch**: plate moves toward/away from the camera, or the measured
  plate face is not in the plane the bar path is assumed to lie in (parallax/scale change);
- **G4c — electronic video stabilization (EIS)**: per-frame warp/crop that changes the effective
  pixel geometry over time and invalidates any fixed camera calibration;
- **G4d — lens distortion**: radial/tangential distortion, mainly near field edges and on
  wide-angle lenses.

## Approach

- Prefer a controlled geometric rig with an independent physical reference (known distances) over
  athlete motion; this may run before the full #58 athlete study.
- Vary one factor at a time against a perpendicular, stabilization-off, standard-lens, centred
  baseline: yaw/tilt angles, plate depth offsets, stabilization on/off, field position (centre,
  edges, corners), and wide-angle lens only if product use might permit it.
- Evaluate with the P3.4 scale-ratio method where the error is a pure scale change, and with
  position/X-Y ROM error where it is not (perspective, distortion, EIS).
- Record per capture: device model, lens/camera mode, resolution, stabilization and zoom state,
  measured camera angle/distance, and plate-plane offset.
- For G4d only: keep camera calibration/undistortion in research tooling (pinned, licence-recorded
  OpenCV in a Python research environment, reusing `research/opencv-tracking/` only if version
  isolation stays clear); do not add OpenCV to the Rust workspace. Record calibration target,
  coverage, camera matrix, distortion coefficients and reprojection error. If undistortion changes
  the pixel coordinate system or crop, it is a P1 transform consumer and must map outputs back.

## Report

Per factor, stratified by normalized field position: physical position error, X/Y ROM error,
mean/peak velocity error where reference-aligned, the factor's measured magnitude, and the cost of
avoiding it (setup burden, field-of-view loss, runtime).

## Decision outcomes

Each factor ends in one of:

- **D1 — negligible inside envelope**: no production change; document evidence in #53.
- **D2 — material only outside a tighter setup/framing rule**: tighten the supported recording
  envelope (e.g. maximum camera angle, stabilization off, keep the bar path away from field edges);
  no production correction unless the tighter rule is operationally unacceptable.
- **D3 — material throughout the useful envelope and correction reliably helps**: open a separate
  ADR/implementation decision covering how production obtains trustworthy camera parameters,
  coordinate-transform integration, and mobile runtime/dependency cost.
- **D4 — correction is inconsistent or creates new failure modes**: reject correction; keep
  envelope guidance and warnings.

## Stop conditions

Do not add production camera calibration, perspective correction or stabilization compensation if:

- error is below practical/reference uncertainty inside the accepted envelope;
- benefit is limited to conditions already classed unsupported;
- recording guidance (D2) solves the observed problem more cheaply;
- per-device calibration burden is disproportionate;
- correction requires unverifiable metadata or introduces unstable transforms.

---

# 12. Phase 5 — controlled marker/fiducial reference tracker

Priority: **low / conditional**  
M0 blocker: **no**  
Detail level: **stub** — expand only when triggered.  
Status: **DEFERRED** (trigger conditions in §12 not met; stub retained).

## Trigger

Implement only if one of these becomes true; otherwise do not implement it:

- manual plate-centre annotation is a significant bottleneck;
- #58/reference acquisition needs a cheap independent secondary trajectory;
- tracker experiments need a controlled target to isolate calibration/kinematics error from
  appearance-based tracking error.

## Question

Can a deliberately distinctive marker (one method only: a chroma circular marker, or one
licensed/open fiducial family already supported by research tooling) give a reference trajectory
accurate enough to replace manual digitisation for its supported conditions?

## Approach

- Implement independently from generic/public algorithm documentation or licensed dependencies;
  never from an unlicensed repository.
- Fail explicitly: no acceptable target means a lost observation, not a guessed coordinate.
- Validate against manual digitisation on development clips: centre MAE/RMSE, availability/loss,
  high-confidence false tracks, and sensitivity to blur, lighting, occlusion and target scale; then
  estimate annotation-time savings.

## Promotion and stop conditions

May be promoted as a **validation aid** (never the athlete-facing production tracker) if it is
materially more accurate/reliable than the annotation burden it replaces, has a narrow documented
envelope, makes failures explicit, and does not contaminate held-out evaluation through tuning.

Stop if manual annotation remains manageable, marker placement costs more than it saves, its error
is not comfortably below the tracker accuracy being evaluated, or lighting/colour sensitivity makes
it unreliable.

---

# 13. Integration with existing #57 tracker plan

`research/PLATE_TRACKING_PLAN.md` remains the authority for tracker candidate experimentation,
freeze and held-out execution.

This plan interacts with it as follows.

### Before #57 freeze

Allowed:

- P1 coordinate-transform correctness if required by a tracker;
- P2.0 real-timestamp survey and P3.0 annotation-oracle study, since neither depends on tracker
  output (development material only);
- P5 marker reference on development data if annotation effort requires it.

Do not:

- change #57 tracker gates;
- inspect #57 held-out data;
- introduce P2–P4 as reasons to keep tuning tracker candidates indefinitely.

### After #57 candidate freeze / selection

Use the selected/frozen tracker configuration as a stable input to P2–P4 whenever possible. This
prevents filter/calibration/camera experiments from being confounded by changing tracker behavior.

### If #57 rejects all trackers

Return to tracker work first. Do not attempt to rescue poor centre tracking by tuning calibration or
filters.

---

# 14. Integration with #58 physical/reference study

P2 and P3 should ultimately consume #58-style independent physical/reference evidence.

Recommended order:

1. obtain at least a minimal qualifying #58 dataset;
2. run unchanged baseline pipeline;
3. freeze P2/P3 development choices;
4. evaluate challengers against exactly the same reference intervals;
5. record deltas relative to baseline;
6. decide whether any challenger merits promotion.

Camera-geometry P4 may use a controlled geometric rig even before the full athlete #58 study if the
rig itself provides an independent physical reference. The P2.0 timestamp survey and the P3.0
annotation-oracle study need no #58 data.

Do not use agreement with another software app as the sole basis for changing M0 kinematic gates.

---

# 15. Evidence artifacts

Each implemented experiment should produce machine-readable and human-readable evidence, but do not
invent persisted schemas until implementation actually needs them.

Suggested local/committed structure:

```text
target/
  research/
    butterworth/
    calibration/
    camera-geometry/
    marker/

docs/
  validation/
    ... normative contract updates only after promotion

docs/
  analysis/
    ... findings / interpreted results when worth retaining
```

`target/` is scratch space: `cargo clean` deletes it and CI does not keep it. An experiment result
counts as retained evidence only once it is committed outside `target/`:

- at every decision point (P2.0, P3.0, each phase's final REJECT / DEFER / CONTINUE RESEARCH /
  CANDIDATE FOR PROMOTION), commit the interpreted findings to `docs/analysis/` together with the
  exact commands, commit and parameters needed to regenerate the `target/research/` outputs;
- commit the machine-readable summary itself (aggregate metrics, applicability fractions, decision)
  only when it is privacy-safe and small; per-frame outputs stay regenerable rather than committed;
- results derived from private material keep only privacy-safe identifiers and aggregates in the
  committed record.

For private fixtures, keep source media, extracted frames, labels and identifying predictions under
the existing private/git-ignored boundaries.

A research result should record:

- Git commit;
- implementation/version;
- parameters;
- input fixture/reference identities or privacy-safe identifiers;
- manifest/reference hash where permitted;
- development/held-out role;
- source/licence of research dependencies;
- deterministic metrics;
- failures/unsupported cases;
- explicit conclusion: reject / continue / candidate-for-promotion.

---

# 16. Test and validation matrix

## Repository gates

Every code-producing work package runs the applicable repository gates from `AGENTS.md`:

```bash
cargo fmt --all -- --check
cargo clippy --locked --workspace --all-targets --all-features -- -D warnings
cargo build --locked --workspace --all-targets --all-features
cargo test --locked --workspace --all-targets --all-features
python -m unittest discover -v -s validation/tests -p 'test_*.py'
python validation/tools/schema_check.py
cargo deny --all-features --locked check
```

Run tracker/filter/analyze/benchmark smoke commands when the work touches those surfaces.
`cargo deny` matters whenever a work package touches `Cargo.toml`/`Cargo.lock`; research tooling
outside the Rust workspace (e.g. a pinned Python OpenCV environment for P4) must still record source,
version and licence as required by §5.

## Experiment-specific minimums

| Experiment | Deterministic synthetic tests | Development evidence | Held-out/reference evidence |
| --- | --- | --- | --- |
| P1 coordinate transforms | required once a consumer exists; invariant doc only until then | when integrated | not normally required |
| P2 Butterworth | required (regularity metric first, filter only after P2.0 passes) | P2.0 real-timestamp survey, then grid | required before promotion |
| P3 multi-frame calibration | required for estimator math | P3.0 annotation oracle | independent reference required before promotion |
| P4 camera geometry | calibration-tool tests where practical | required | controlled-rig/reference evidence required for product decision |
| P5 marker tracker | required | required | only if promoted as formal reference aid |

---

# 17. Decision records and promotion path

Every experiment ends with one of:

- **REJECT** — evidence does not justify complexity;
- **DEFER** — insufficient evidence or no current need;
- **CONTINUE RESEARCH** — promising but missing required evidence;
- **CANDIDATE FOR PROMOTION** — evidence justifies opening production/contract work.

`CANDIDATE FOR PROMOTION` is not equivalent to merged production behavior.

For promotion:

### Butterworth

Review:

- whether `FilterConfig` expands;
- provenance/version semantics;
- CLI `--filter` exposure;
- filter experiment schema/version;
- canonical analysis fixtures/golden output;
- docs/validation updates.

### Multi-frame calibration

Require:

- new calibration method/version;
- schema/persistence review;
- exact provenance for contributing frames/estimator;
- migration/backward compatibility decision.

### Camera-geometry correction

Applies only to a D3 outcome; D1/D2/D4 change recording guidance (#53), not code.

Require:

- ADR if a native CV/device calibration dependency or preprocessing boundary changes;
- reproducible device/intrinsic provenance;
- coordinate-transform integration;
- mobile/runtime packaging evaluation.

### Marker reference

If only a validation tool:

- keep it outside production crates where possible;
- document its evidence class and supported conditions.

---

# 18. Stop-loss rules for project complexity

These rules are intentionally stronger than "keep experimenting until something works".

1. Do not add a production dependency to reproduce a technique already matched by simpler existing
   code.
2. Do not change a persisted schema for research-only output.
3. Do not add automatic resampling to make a regular-sampling filter applicable.
4. Do not replace a transparent calibration method when independent error does not improve.
5. Do not add camera undistortion, perspective correction or stabilization compensation when
   recording guidance solves the observed problem more cheaply.
6. Do not make controlled markers part of the normal athlete workflow unless a separate product
   decision explicitly chooses that trade-off.
7. Do not let P2–P5 postpone the #57/#58 re-entry decision when the existing evidence is already
   sufficient to decide.
8. Reject a technique when its measured benefit is smaller than annotation/reference uncertainty or
   normal run-to-run variability.
9. Prefer narrowing the supported envelope over hiding unsupported geometry with heuristic
   correction.
10. Preserve a failed experiment as useful evidence; do not quietly retune against held-out data.

---

# 19. Suggested implementation issues

Do not create these automatically from this plan unless the project owner decides to execute them.
When execution starts, prefer one focused issue/PR per experiment.

Suggested issue breakdown:

### Issue A — geometry transform contract

Now (docs only): `[Docs] Record the display-coordinate invariant for tracker observations`

- invariant text in `docs/validation/TRACKER_EXPERIMENTS.md` (see Phase 1, "Current state").

Only when a transformed-frame consumer appears:
`[Research] Prove coordinate transforms for resized/cropped inference`

Deliverables:

- minimal transform representation in the actual consumer boundary;
- inverse/round-trip tests;
- non-uniform resize calibration regression;
- docs update.

### Issue B — Butterworth challenger

`[Research] Benchmark zero-phase Butterworth under timestamp regularity constraints`

Deliverables:

- applicability metric;
- P2.0 real-timestamp survey and go/no-go decision (stop here on REJECT);
- research-only filter implementation with pinned order/cutoff convention;
- development parameter grid;
- synthetic evidence artifact;
- later held-out/reference result and explicit decision.

### Issue C — calibration robustness

`[Research] Compare single-seed and multi-frame plate-diameter calibration`

Deliverables:

- P3.0 annotation-oracle study and go/no-go decision (stop here on REJECT);
- offline estimator harness, once a validated per-frame size source exists;
- robust diameter diagnostics;
- independent-reference comparison via relative scale error;
- separate calibration-vs-quality conclusions.

### Issue D — camera geometry

Create only when triggered (see Phase 4):

`[Research] Quantify camera-geometry error sources on the M0 recording envelope`

Deliverables:

- controlled-rig study of camera yaw/tilt, plate-plane depth, stabilization and field position;
- pinned calibration tooling and representative device calibration, for lens distortion only;
- explicit D1/D2/D3/D4 decision per factor.

### Issue E — marker reference

Create only when triggered:

`[Research] Evaluate controlled marker tracking as a validation reference aid`

---

# 20. Deferred product ideas captured from the review

These are intentionally **not** part of the implementation phases above:

- automatic rep/phase detection with manual correction;
- persisted provenance for manual rep-boundary overrides;
- time-normalized rep trajectory comparison;
- mean trajectory and variability bands;
- load-velocity profiling;
- velocity-loss set feedback;
- audible/haptic VBT feedback.

Revisit these only after the M0 measurement chain and rep/event semantics are validated.

---

# 21. Recommended execution order from current repository state

Given the repository state when this plan was authored:

1. continue the existing `research/PLATE_TRACKING_PLAN.md` / #57 work;
2. document the P1 display-coordinate invariant now; implement P1.1–P1.3 only when tracker
   preprocessing actually needs coordinate mapping;
3. run the P2.0 real-timestamp applicability survey first: it is cheap, needs no #58 data, and
   decides whether any Butterworth implementation is worth doing;
4. run the P3.0 annotation-oracle study when enough deliberately fitted per-frame diameters exist;
5. acquire/complete enough #58 independent reference evidence to establish a baseline;
6. if P2.0 passed, run P2 Butterworth as the first full challenger because it is relatively isolated
   and directly affects velocity accuracy;
7. if P3.0 passed and a validated per-frame size source exists, run P3 multi-frame calibration;
8. run P4 camera-geometry sensitivity if #53/reference evidence indicates setup- or field-position-
   dependent error worth explaining;
9. implement P5 only if manual reference generation becomes a meaningful bottleneck;
10. make explicit promotion/rejection decisions before adding any production surface.

The default outcome is allowed to be **no production change**. The goal is better evidence, not
feature accumulation.
