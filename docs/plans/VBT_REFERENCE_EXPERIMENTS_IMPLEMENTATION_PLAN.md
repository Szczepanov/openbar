# VBT reference experiments — implementation plan

Status: proposed execution plan  
Related: #2, #57, #58, #53, ADR-0004, ADR-0008, `research/PLATE_TRACKING_PLAN.md`  
Research basis: `docs/analysis/VBT_OPEN_SOURCE_REFERENCE_REVIEW.md`

## 1. Purpose

Convert the VBT/CV reference review into an executable sequence of experiments without turning
interesting external techniques into automatic OpenBar requirements.

This plan covers five follow-up areas:

1. image-coordinate transform correctness;
2. zero-phase Butterworth filtering as a challenger family;
3. multi-frame plate-size calibration robustness;
4. camera-intrinsic/lens-distortion sensitivity;
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

## 2. Current state

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

### G4 — Lens distortion

Does lens distortion materially harm measurements inside the intended side-view recording envelope,
and does calibrated undistortion improve those measurements enough to justify operational or runtime
complexity?

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
       P2 Butterworth    P3 multi-frame      P4 distortion
          challenger       calibration         sensitivity
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

Priority: **high**  
M0 blocker: **no**, unless a current research/production tracker performs transformed-frame
inference and returns transformed coordinates.

## Rationale

A fixed resize can change X/Y scale differently. Any future model pipeline may crop, resize,
letterbox or rotate input for inference. Calibration is valid only when observations are mapped back
into the canonical coordinate space.

The current repository search found no general crop/resize/letterbox coordinate-transform
abstraction. Do not add a broad abstraction unless an actual consumer needs it; implement the
smallest reusable representation required by the first transformed-frame path.

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
invariant and defer implementation rather than creating unused framework code.

---

# 9. Phase 2 — Butterworth challenger experiment

Priority: **medium/high after baseline reference data exists**  
M0 blocker: **no by default**

## Rationale

A reviewed public VBT project uses a fourth-order zero-phase Butterworth low-pass filter with an
8 Hz cutoff. That is sufficient to justify evaluation, not adoption.

Unlike the existing timestamp-aware Savitzky-Golay and Kalman candidates, a conventional digital
Butterworth implementation assumes regular sample spacing. OpenBar must explicitly test that
assumption rather than substituting nominal FPS.

## P2.1 — define timestamp regularity

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

Predeclare a small grid before evaluating held-out scenarios.

Suggested initial grid:

- order: 4;
- cutoff: 4, 6, 8, 10, 12 Hz where valid for the measured sampling frequency;
- `max_gap_s`: keep aligned with the existing experiment unless evidence requires a separate
  development grid.

Do not add more degrees of freedom until the first grid shows a reason.

Selection rule should remain consistent with existing family selection:

1. minimize mean development velocity RMSE;
2. use mean position RMSE as deterministic tie-break;
3. never use held-out data for tuning.

## P2.4 — synthetic applicability/behavior tests

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

- Butterworth is not applicable to realistic timestamp distributions;
- held-out improvement is negligible relative to reference/annotation uncertainty;
- peak/edge behavior is worse without a compensating benefit;
- a current family performs equivalently with fewer assumptions;
- using Butterworth would require silent resampling or nominal-FPS assumptions.

---

# 10. Phase 3 — multi-frame calibration robustness

Priority: **medium after tracker quality is adequate**  
M0 blocker: **no by default**

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

The current tracker contract primarily measures centre/bounds according to candidate behavior. Reuse
existing bounds/radius evidence where trustworthy; do not add a new ML model just to run this study.

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

Against #58-compatible physical/reference cases compare each estimator on:

- calibrated position error;
- vertical ROM error;
- horizontal ROM/error when geometry makes it meaningful;
- mean/peak velocity effect where scaling affects the definition;
- sensitivity to plate position in frame;
- sensitivity to fast movement/blur;
- sensitivity to foreshortening/depth change.

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

Keep `PlateDiameterCalibration@1` unchanged if:

- S1/S2/S3 do not materially improve physical error;
- results are dominated by tracker/bounds noise;
- improvement disappears on held-out cases;
- the method requires silent per-frame rescaling;
- added complexity exceeds measurable benefit.

Even if calibration promotion stops, retain useful evidence about diameter variation for #53 if
validated.

---

# 11. Phase 4 — lens-distortion sensitivity

Priority: **medium/low until recording-envelope evidence indicates need**  
M0 blocker: **no by default**

## Rationale

OpenBar already records lens distortion as a calibration limitation. The question is not whether
distortion exists, but whether it materially affects OpenBar's intended measurement envelope.

## P4.1 — research tooling boundary

Preferred approach:

- keep camera calibration/undistortion in research tooling;
- use independently licensed OpenCV tooling (Python research environment is acceptable);
- do not add OpenCV to the Rust workspace merely for this experiment;
- pin OpenCV/package versions and record licence/source.

Reuse the existing research OpenCV environment only if dependency/version isolation remains clear;
otherwise create a small dedicated `research/camera-calibration/` environment.

## P4.2 — intrinsic calibration capture

For each representative device/lens mode:

- record device model;
- selected lens/camera mode;
- resolution;
- stabilization/zoom state;
- calibration target type and printed/measured dimensions where applicable;
- number and coverage of calibration images;
- camera matrix/distortion coefficients;
- calibration reprojection error;
- exact tooling/version.

Use chessboard or ChArUco according to the selected OpenCV method. The target choice itself is not a
product requirement.

## P4.3 — sensitivity fixture design

Capture controlled plate/reference positions across the image field:

- centre;
- left/right;
- upper/lower;
- near useful envelope edges.

Prefer a controlled geometric rig/reference where source position is known. If using barbell motion,
keep camera and movement plane fixed and use independent reference data.

Test at least:

- ordinary/standard phone lens used by intended workflow;
- wide-angle only if product use might permit it.

## P4.4 — two-path comparison

For each case:

```text
original decoded frame
        |
        +--> unchanged OpenBar measurement
        |
        +--> calibrated undistortion
                 |
                 v
          same measurement logic
```

Keep tracker/filter/calibration settings otherwise matched.

If undistortion changes the pixel coordinate system/crop, record the transform and map outputs
consistently.

## P4.5 — report

Stratify by normalized field position and report:

- plate-centre/reference position error;
- physical position error;
- X/Y ROM error;
- mean/peak velocity error where reference-aligned;
- distortion correction residual/reprojection diagnostics;
- runtime;
- setup burden;
- effective field-of-view/cropping changes.

## Decision outcomes

The study must end in one of these explicit outcomes:

### D1 — negligible inside envelope

Action:

- keep production pipeline unchanged;
- document evidence in #53/recording guidance;
- reject production undistortion.

### D2 — material only near edges

Action:

- prefer a tighter supported recording/framing envelope;
- no production correction unless tighter framing is operationally unacceptable.

### D3 — material throughout useful envelope and correction reliably helps

Action:

- open a separate architecture/implementation decision for device calibration/undistortion;
- evaluate how production obtains trustworthy intrinsics;
- evaluate mobile runtime/binary/dependency cost.

### D4 — correction is inconsistent or creates new failure modes

Action:

- reject correction;
- retain warning/envelope guidance.

## Stop conditions

Do not add production camera calibration if:

- error is below practical/reference uncertainty inside the accepted envelope;
- correction benefit is limited to conditions already classed unsupported;
- per-device calibration burden is disproportionate;
- correction requires unverifiable metadata or introduces unstable transforms.

---

# 12. Phase 5 — controlled marker/fiducial reference tracker

Priority: **low / conditional**  
M0 blocker: **no**

## Trigger

Implement this only if one of these becomes true:

- manual plate-centre annotation is a significant bottleneck;
- #58/reference acquisition needs a cheap independent secondary trajectory;
- tracker experiments need a controlled target to isolate calibration/kinematics error from
  appearance-based tracking error.

If none is true, do not implement it.

## P5.1 — choose the simplest controlled target

Start with one method:

- highly distinctive chroma circular marker; or
- a licensed/open fiducial marker family already supported by research tooling.

Do not evaluate many marker families initially.

## P5.2 — independent implementation

Implement from generic/public algorithm documentation or licensed dependencies.

For a chroma baseline:

- deterministic color-space threshold;
- morphology parameters;
- connected-component/contour selection;
- circle/centroid estimate;
- explicit loss when no acceptable target exists;
- confidence/quality diagnostic based on documented geometric/color evidence.

Do not copy implementation from an unlicensed repository.

## P5.3 — validation against manual digitisation

Use controlled development clips and compare:

- centre MAE/RMSE;
- availability/loss;
- high-confidence false tracks;
- blur sensitivity;
- lighting sensitivity;
- occlusion sensitivity;
- target scale/distance;
- runtime.

Then estimate annotation-time savings if the purpose is reference generation.

## Promotion decision

This tool may be promoted as a **validation aid** if it:

- is materially more accurate/reliable than the annotation burden it replaces;
- has a narrow documented recording envelope;
- makes failures explicit;
- does not contaminate held-out evaluation through tuning.

It does not become the athlete-facing production tracker merely because it performs well with a
special marker.

## Stop conditions

Do not continue if:

- manual annotation remains manageable;
- marker placement adds more operational burden than it saves;
- error is not comfortably below the tracker accuracy being evaluated;
- lighting/color sensitivity makes the reference unreliable.

---

# 13. Integration with existing #57 tracker plan

`research/PLATE_TRACKING_PLAN.md` remains the authority for tracker candidate experimentation,
freeze and held-out execution.

This plan interacts with it as follows.

### Before #57 freeze

Allowed:

- P1 coordinate-transform correctness if required by a tracker;
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

Lens-distortion P4 may use a controlled geometric rig even before the full athlete #58 study if the
rig itself provides an independent physical reference.

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
    distortion/
    marker/

docs/
  validation/
    ... normative contract updates only after promotion

docs/
  analysis/
    ... findings / interpreted results when worth retaining
```

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
```

Run tracker/filter/analyze/benchmark smoke commands when the work touches those surfaces.

## Experiment-specific minimums

| Experiment | Deterministic synthetic tests | Development evidence | Held-out/reference evidence |
| --- | --- | --- | --- |
| P1 coordinate transforms | required | when integrated | not normally required |
| P2 Butterworth | required | required | required before promotion |
| P3 multi-frame calibration | required for estimator math | required | independent reference required before promotion |
| P4 lens distortion | calibration-tool tests where practical | required | controlled/reference evidence required for product decision |
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

### Lens correction

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
5. Do not add camera undistortion when recording guidance solves the observed problem more cheaply.
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
- research-only filter implementation;
- development parameter grid;
- synthetic evidence artifact;
- later held-out/reference result and explicit decision.

### Issue C — calibration robustness

`[Research] Compare single-seed and multi-frame plate-diameter calibration`

Deliverables:

- offline estimator harness;
- robust diameter diagnostics;
- independent-reference comparison;
- separate calibration-vs-quality conclusions.

### Issue D — lens distortion

`[Research] Quantify camera lens-distortion impact on the M0 recording envelope`

Deliverables:

- pinned calibration tooling;
- representative device calibration;
- field-position study;
- explicit D1/D2/D3/D4 decision.

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
2. implement P1 only where current/future tracker preprocessing actually needs coordinate mapping;
3. acquire/complete enough #58 independent reference evidence to establish a baseline;
4. run P2 Butterworth as the first challenger because it is relatively isolated and directly affects
   velocity accuracy;
5. run P3 multi-frame calibration if per-frame plate-size evidence is trustworthy;
6. run P4 lens-distortion sensitivity if #53/reference evidence indicates field-position/calibration
   error worth explaining;
7. implement P5 only if manual reference generation becomes a meaningful bottleneck;
8. make explicit promotion/rejection decisions before adding any production surface.

The default outcome is allowed to be **no production change**. The goal is better evidence, not
feature accumulation.
