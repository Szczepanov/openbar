# M0 bar-path measurement — experiment plan

Status: proposed  
Related: #57, #53, #59, #95, ADR-0004, ADR-0008, ADR-0009  
Analysis basis: [M0_BAR_PATH_MEASUREMENT_STRATEGY.md](../analysis/M0_BAR_PATH_MEASUREMENT_STRATEGY.md)  
Existing tracker work: [TRACKER_BAKEOFF_PLAN.md](TRACKER_BAKEOFF_PLAN.md), [PLATE_GEOMETRY_REFINEMENT_PLAN.md](PLATE_GEOMETRY_REFINEMENT_PLAN.md), [research/PLATE_TRACKING_PLAN.md](../../research/PLATE_TRACKING_PLAN.md)  
Existing reference work: [VBT_REFERENCE_EXPERIMENTS_IMPLEMENTATION_PLAN.md](VBT_REFERENCE_EXPERIMENTS_IMPLEMENTATION_PLAN.md) (Phase 5 marker stub, advanced here as Phase 1), [VBT_OPEN_SOURCE_REFERENCE_REVIEW.md](../analysis/VBT_OPEN_SOURCE_REFERENCE_REVIEW.md)

## 1. Purpose

Test whether a barbell-specific measurement pipeline can outperform the current generic/video
tracker candidates on the quantities M0 ultimately cares about, without contaminating held-out
evidence or changing production contracts prematurely.

The hypothesis is:

> A coarse localizer plus plate-specific absolute geometry, sub-pixel relative registration and an
> offline global trajectory fit can estimate bar-axis displacement more accurately and efficiently
> than treating every frame's generic tracker centre as the complete measurement.

This plan deliberately separates:

    implement experiment
            |
            v
    evaluate development evidence
            |
            v
       freeze candidate
            |
            v
     evaluate held-out evidence
            |
            v
       explicit decision
       /             \
    reject          promote
                       |
                       v
             separate production work

A research win is not permission to alter M0 production behavior.

## 2. Current state and constraints

### 2.1 Existing evidence to preserve

Do not rerun or reinterpret existing evidence merely to obtain a preferred outcome.

Current development baselines include:

- opencv-csrt;
- the existing CSRT geometry-refinement candidates;
- SAM 2.1 small-circle;
- SAM 2.1 base-plus-circle;
- the historical OpenBar template/contrast baselines.

The current SAM circle candidates are the strongest shippable accuracy-reference family from the
existing bake-off, but they have not passed every #57 gate.

### 2.2 Existing contracts to reuse

Experiments should reuse where possible:

- ADR-0006 decode/timestamp contract;
- ADR-0007 display pixel-coordinate convention;
- manual-target-seed-v1;
- tracker-prediction-v1;
- openbar-cli benchmark;
- analyze --observations;
- PlateDiameterCalibration@1;
- existing filter and canonical kinematics implementations.

Do not create a new prediction schema merely to hold research diagnostics. Place candidate-specific
diagnostics in research sidecars until a production use case requires a persisted contract.

### 2.3 Held-out boundary

Development and validation fixtures in the #57 manifest retain their existing role.

Rules:

- tune parameters only on development clips;
- do not run new parameterized candidates on held-out clips before the freeze;
- a held-out clip accidentally exposed to a candidate before freeze is contaminated for that
  selection round;
- the final held-out run follows the same one-shot discipline as research/PLATE_TRACKING_PLAN.md;
- personal VBT clips may inform development hypotheses only when explicitly kept outside #57
  validation material.

## 3. Goals

The plan should answer six questions.

### G1 — Relative-motion value

Does direct frame-to-frame image registration estimate delta-X/delta-Y more accurately than
subtracting two independently estimated absolute centres?

### G2 — Plate-specific absolute metrology

Can a lightweight raw-pixel geometry estimator provide stable absolute plate-centre anchors without
the failure modes seen when geometry was fed back into CSRT?

### G3 — Fusion

Does combining absolute anchors with relative displacement reduce temporal jitter and drift while
preserving explicit loss and fast motion?

### G4 — Downstream value

Do improvements in position evidence materially improve definition-matched mean and peak velocity,
rather than only making overlays or centre MAE look better?

### G5 — Bottleneck isolation

Can a controlled high-contrast same-video marker determine whether natural-plate localization is
still the dominant source of error?

### G6 — Learned localizer value

Only if coarse localization/reacquisition is limiting: does a tiny learned localizer such as
YOLO26n materially improve robustness/runtime enough to justify its dataset, licensing and
deployment cost?

## 4. Non-goals

This plan does not authorize:

- automatic plate detection in the M0 product;
- a YOLO/Ultralytics production dependency;
- training on held-out validation clips;
- replacing manual seeds;
- changing PlateDiameterCalibration@1;
- dynamic per-frame physical scale;
- perspective correction without a dedicated study;
- filling lost samples through smoothing or optimization;
- changing filter defaults;
- changing canonical mean/peak definitions;
- UI/Flutter/live-camera work;
- using WL Analysis as ground truth;
- changing M0 gates merely because a new diagnostic metric is introduced.

## 5. Experiment metrics

### 5.1 Existing metrics remain required

For every candidate retain:

- plate-centre MAE/RMSE and percentiles;
- availability/loss;
- maximum consecutive loss;
- false-track diagnostics;
- runtime;
- deterministic/repeatability evidence where applicable.

### 5.2 Add research-only motion diagnostics

Add research-sidecar/report metrics without changing validation schemas initially.

For matched labelled samples:

1. absolute X/Y error;
2. Euclidean centre error;
3. per-axis displacement error between consecutive labelled samples:
   - delta-X prediction minus delta-X label;
   - delta-Y prediction minus delta-Y label;
   - reported with the label interval length (frames and seconds), because the current development
     labels are sparse (about 20 per clip) and consecutive labels are generally not adjacent frames;
4. Euclidean delta-position error;
5. de-biased trajectory error:
   - estimate one robust constant X/Y offset on the development comparison interval;
   - report residual error after removing that offset;
   - never use the de-biased trajectory as canonical output;
   - this removes only a constant offset, not the motion-linked component described in the
     analysis §4.1;
6. error-versus-labelled-velocity correlation per axis, so the motion-linked component stays
   visible. It is a diagnostic only; research/PLATE_TRACKING_PLAN.md forbids tuning trackers to it;
7. cumulative relative-displacement drift from the seed;
8. temporal jitter on deliberately stationary or near-stationary intervals where ground truth
   permits;
9. relative-displacement availability.

Label-delta noise is about sqrt(2) times the per-label annotation error, so delta metrics must be
read against annotation repeatability (pass B, #57). Frame-to-frame jitter is not observable from
sparse labels; measure it on a short densely labelled development window, on the Phase 1 marker
trajectory, on static intervals, or on synthetic sequences.

For kinematic/reference cases when an independent definition-matched source is available:

- calibrated position error;
- ROM error;
- mean signed-axis velocity error;
- peak signed-axis velocity error;
- velocity RMSE;
- peak timing error;
- failure/unsupported count.

The research report must make clear which metrics use manual labels and which use independent
physical/reference data.

## 6. Phase 0 — baseline freeze and measurement harness

Priority: immediate  
Production change: none

### P0.1 — record baseline

Record:

- current git commit;
- private manifest SHA-256;
- development fixture IDs;
- held-out fixture IDs without running the new candidates;
- manual annotation/repeatability state;
- baseline candidate versions/configurations;
- current calibration/filter/kinematic provenance.

Reuse existing #57 freeze/evidence artifacts when available instead of creating a second source of
truth.

### P0.2 — implement development-only delta metrics

Extend research comparison tooling, not the production benchmark contract, to calculate the
research metrics in §5.2.

Requirements:

- exact labelled timestamp/frame matching;
- no interpolation;
- no coordinate for lost samples;
- delta metrics require both endpoints to have labels and candidate observations;
- separately count unavailable intervals;
- seed-excluded metrics remain available;
- deterministic JSON/Markdown summaries;
- paired candidate-vs-baseline comparisons.

Likely location:

- research/opencv-tracking/compare.py, or
- a small research-only shared evaluator under research/.

Do not duplicate authoritative core metric definitions for ROM/mean/peak velocity.

### P0.3 — synthetic sanity fixtures

Before real clips, generate deterministic synthetic observation sequences proving:

- constant spatial bias raises centre MAE but leaves delta-position unchanged;
- independent per-frame jitter propagates into displacement/velocity;
- accumulated relative measurements drift without absolute anchors;
- loss breaks delta intervals rather than creating fake zero movement.

### Acceptance

- existing benchmark results are unchanged;
- new research metrics are reproducible;
- no held-out fixture has been processed by a tunable new candidate;
- no production schema/version changes.

## 7. Phase 1 — controlled marker same-video reference

Priority: high  
Purpose: isolate the bottleneck  
Production change: none

This advances the previously deferred controlled-marker experiment because the tracker bake-off now
shows enough residual ambiguity to justify it.

### P1.1 — target design

Use a removable, high-contrast circular target rigidly aligned with the visible bar axis. The exact
physical design is owner-controlled and must not obstruct lifting safety.

This advances the deferred Phase 5 stub in
`docs/plans/VBT_REFERENCE_EXPERIMENTS_IMPLEMENTATION_PLAN.md` §12, whose triggers are now met by
the residual tracker ambiguities.

Record:

- marker physical diameter;
- mounting relationship to the bar axis and centring tolerance;
- marker colour/material;
- camera/lens/capture settings;
- whether the natural plate remains simultaneously visible.

The marker should be large enough for stable localization but should not become the physical scale
reference by default. Keep plate/stick scale experiments separate. Because a sleeve-end marker sits
in a different depth plane than the plate face, estimate and report a fixed marker-to-plate pixel-scale
ratio per clip to allow physically meaningful trajectory comparisons without altering the plate scale.

### P1.2 — independent marker tracker

Implement from first principles in research code:

1. convert ROI to a colour representation appropriate to the selected marker;
2. segment the marker using parameters selected on development material only;
3. morphology only if justified and recorded;
4. connected components / expected-location gating;
5. robust circle or ellipse fit;
6. sub-pixel centre refinement where feasible;
7. diagnostics:
   - area;
   - circularity;
   - radius/axes;
   - fit residual;
   - arc/contour support;
   - mask purity;
   - loss reason.

Do not copy BarbellCV source code; its repository has no explicit licence.

### P1.3 — compare marker with manual labels and plate tracker

For development clips with both natural plate labels and marker visibility:

- marker centre vs manually annotated bar/plate centre after accounting for fixed mounting offset
  and the depth scale ratio;
- marker delta-position vs label delta-position (measured across matched labelled intervals);
- marker temporal jitter on dense or static windows;
- marker availability under fast motion;
- sleeve-rotation effects (identifying whether off-axis marker mounting introduces rotation-synchronous
  periodic residuals during turnover);
- blur/lighting failure cases;
- tripartite comparison (marker vs natural plate candidate vs manual labels / reference) to isolate
  whether motion-linked bias originates from the labeller or the vision estimators.

### Decision

- **GO:** marker evidence is materially more stable than natural tracking and good enough to
  distinguish localization error from downstream error;
- **REJECT:** marker is itself too noisy/blurred/ambiguous or mounting rotation dominates error;
- **DEFER:** insufficient same-video data.

The marker remains a secondary development/reference aid, never an athlete-facing requirement.

## 8. Phase 2 — plate-specific absolute metrology

Priority: high  
Production change: none

### P2.1 — ROI source

Begin with the simplest existing coarse localizer that keeps the plate in the ROI reliably on
development clips. CSRT or a SAM-derived ROI may be used as a research input.

The coarse center must not be scored as the final candidate output.

Predeclare ROI expansion relative to the seed plate diameter. Tune only on development clips.

### P2.2 — radial edge candidate

Implement a deterministic candidate:

1. grayscale or luminance representation of the source-resolution ROI;
2. optional light denoise with fixed documented parameters;
3. from the predicted centre, cast a fixed number of angular rays;
4. along each ray search only inside a radius band around the expected plate rim;
5. compute radial gradient evidence;
6. retain the strongest plausible rim transition when it passes edge/contrast rules;
7. refine edge position to sub-pixel location from a local gradient model;
8. robustly fit circle and optionally ellipse geometry;
9. refine on inliers;
10. emit the centre only when geometry gates pass.

Development grid should be intentionally small, for example:

- ray count: 48 / 72 / 96;
- radius search band: two or three predeclared widths;
- circle versus ellipse fit;
- one robust loss/inlier tolerance family.

Do not recreate a large tracker zoo.

### P2.3 — radial-symmetry candidate

Implement or independently reproduce the published radial-symmetry-centre concept as a second
precision estimator on the same ROI.

The research implementation must cite the paper and be derived independently from the published
method. If external reference code is consulted, record its licence before reuse.

### P2.4 — blur diagnostics

Record, but do not initially tune on:

- per-ray edge width/sharpness;
- estimated direction of coarse motion;
- support on rim sectors parallel/perpendicular to motion.

Only add motion-aware weighting if base results show a repeatable blur-linked failure.

### P2.5 — no feedback reinitialization

Do not feed refined centre/radius back into CSRT during this phase. The earlier geometry work showed
that reinitialization can lock onto inner plate/collar/background edges.

The experiment tests the measurement layer independently of coarse tracker state.

### Acceptance

A candidate is interesting enough for Phase 4 fusion if on development data it:

- materially improves delta-position error or temporal jitter over the coarse tracker;
- does not create a worse failure mode on another represented lift;
- has explicit loss instead of confident false coordinates;
- runs comfortably faster than the current neural reference on desktop.

No fixed percentage defines "materially"; compare against annotation repeatability and the eventual
physical-reference uncertainty.

## 9. Phase 3 — sub-pixel relative registration

Priority: high  
Production change: none

### P3.1 — registration patch

Use a plate-centred ROI or annular region where possible to reduce contamination from the lifter and
background.

The patch transform between frames must be mapped back to canonical display coordinates.

Because plates rotate freely with the barbell sleeve (particularly in the catch and turnover of
dynamic lifts), candidate registration models must account for rotation:
- restrict registration to circularly symmetric / rotation-invariant rim profiles, OR
- estimate rigid Euclidean motion (translation + rotation, e.g. via Fourier-Mellin or ECC Euclidean)
  rather than pure translation, OR
- expose a rotation/correlation diagnostic that flags or rejects frames corrupted by spinning texture.

### P3.2 — candidate A: phase/local DFT registration

Implement a deterministic translation or similarity estimator based on phase correlation with local
sub-pixel refinement.

Starting point:
- Guizar-Sicairos et al., 2008, DOI 10.1364/OL.33.000156.

Do not assume whole-ROI pure translation without rotation gating. The candidate must expose a
registration-quality diagnostic and fail when the model is not supported.

### P3.3 — candidate B: ECC/robust local alignment

Optionally compare one inexpensive alternate registration family, such as ECC translation/affine,
if the first candidate leaves an identifiable failure mode.

Keep this phase small. Its goal is to test the relative-measurement hypothesis, not compare every
registration algorithm.

### P3.4 — forward/backward consistency

Where feasible:

- estimate t -> t+1;
- estimate t+1 -> t;
- compose them;
- use residual inconsistency as a quality/loss diagnostic.

### P3.5 — scoring

Score directly against labelled consecutive displacement, not only accumulated centre.

Report:

- delta-X/Y MAE/RMSE;
- delta-position percentiles;
- availability;
- cumulative drift when integrated from the seed;
- runtime per frame;
- quality-vs-error relationship.

### Stop condition

Reject relative registration if it cannot outperform simply subtracting the best absolute estimator
on development delta-position/velocity-relevant metrics, or if quality diagnostics cannot identify
catastrophic misregistration.

## 10. Phase 4 — deterministic global trajectory fusion

Priority: after Phases 2–3 produce useful evidence  
Production change: none

### P4.1 — model

For frame centres C_t, create research constraints from:

- absolute observations G_t;
- relative observations D_t;
- optional seed anchor.

A simple first objective is robust weighted least squares:

    sum_t wG_t * rho(C_t - G_t)
    +
    sum_t wD_t * rho((C_t - C_(t-1)) - D_t)

Use a deterministic robust loss and deterministic solver.

Do not add an unconstrained "smoothness" term in the first version. The purpose is to combine
measured evidence, not create a plausible trajectory.

### P4.2 — loss semantics

A frame/interval without sufficient visual evidence must not be converted to a coordinate merely
because neighboring constraints exist.

Possible implementation rule:

- solve only connected segments for which a documented minimum evidence condition is met;
- emit lost at frames that lack the evidence required by the candidate policy;
- never bridge a gap longer than a predeclared research threshold;
- keep all raw absolute/relative diagnostics in sidecars.

### P4.3 — confidence

Do not invent a calibrated probability.

Candidate confidence may be a deterministic diagnostic score composed from:

- absolute fit quality;
- relative registration quality;
- agreement between the two estimates;
- support count.

Before any promotion, confidence-vs-error must be measured explicitly because current SAM
confidence already demonstrated that model confidence can remain high while position is wrong.

### P4.4 — output

Map the fused result to tracker-prediction-v1:

- canonical display coordinates;
- authoritative timestamps;
- explicit tracked/lost;
- reproducible implementation version/config;
- no runtime field when byte-stable output is required by the existing workflow.

All additional evidence stays in a research sidecar.

### Acceptance

Advance to freeze if the fused candidate:

- improves development delta-position and/or downstream physical metrics;
- preserves or improves absolute tracking accuracy;
- reduces drift relative to integrated relative registration;
- has no new silent false-track class;
- is deterministic on CPU, or has documented repeatability bounds if using GPU input evidence.

## 11. Phase 5 — YOLO26 research localizer, conditional

Priority: conditional / low until coarse localization is shown limiting  
Production change: explicitly none

### Trigger

Do not start this phase merely because YOLO26n is small.

Start only if the preceding experiments show that:

- precision metrology works when the correct ROI is supplied, but
- CSRT/manual-motion prediction cannot keep/reacquire the plate reliably enough.

### P5.1 — licence quarantine

Record before any work:

- exact Ultralytics commit/version;
- code licence;
- model/checkpoint licence;
- dataset rights;
- whether the experiment can legally be performed under the intended conditions.

Under the current vendor licensing, Ultralytics YOLO is AGPL-3.0 by default and commercial/
proprietary integration requires separate commercial licensing according to Ultralytics. Therefore:

- research artifacts remain isolated;
- no Ultralytics code/weights are added to the MIT production dependency graph;
- no production design depends on YOLO26 unless a separate licensing decision is made.

### P5.2 — data split

COCO does not contain a plate/barbell class, so create a custom development dataset if the phase
starts.

Rules:

- development media only;
- no held-out #57 frames in training/validation;
- keep private media/labels under validation/private or another git-ignored research location;
- document annotation semantics separately for detection, segmentation or keypoint tasks;
- freeze training configuration before any held-out run.

### P5.3 — candidate order

Evaluate only what answers a concrete need:

1. YOLO26n detect -> coarse ROI;
2. YOLO26n segmentation -> mask -> existing plate-specific geometry;
3. custom YOLO26n pose/keypoint -> bar-axis centre only if segmentation/localization remains
   inadequate.

Do not use detector box centre as the default physical observation.

P2/stride-4 may be tested only if small-object localization is a demonstrated limitation. Official
YOLO26 P2 configs are architecture-only; no P2 pretrained scale weights are currently released, so
this adds training cost.

### P5.4 — metrics

Primary:

- ROI recall on the intended plate;
- reacquisition latency after controlled loss/occlusion;
- failure/false-detection rate;
- downstream precision-estimator availability;
- total pipeline runtime/memory;
- centre/delta/velocity error after geometric refinement.

Secondary:

- detection/segmentation mAP.

### Stop conditions

Reject/defer YOLO26 if:

- the current coarse localizer already meets the required ROI/reacquisition need;
- it does not improve the downstream measurement;
- dataset size/generalization is inadequate;
- licence cost/obligation is disproportionate;
- runtime/memory prevents Pixel 8 viability;
- a permissively licensed/localizer alternative provides equivalent evidence.

## 12. Phase 6 — development selection and freeze

Prerequisites:

- Phase 0 metric harness complete;
- at least one useful candidate from Phases 2–4;
- marker/reference diagnostics completed or explicitly rejected/deferred;
- annotation repeatability understood enough to interpret the 3 px gate.

### P6.1 — shortlist

Shortlist at most two complete measurement pipelines.

A complete pipeline includes:

- coarse localizer;
- absolute estimator;
- optional relative estimator;
- fusion policy;
- confidence/loss policy;
- all parameters;
- environment/dependency/model provenance.

### P6.2 — selection dimensions

Do not select from one scalar score.

Review:

- absolute centre error;
- delta-position error;
- availability/loss;
- false tracks;
- temporal jitter;
- downstream development kinematics where valid;
- runtime/memory;
- determinism/repeatability;
- licence/deployment feasibility.

### P6.3 — freeze artifact

Reuse or extend the existing #57 freeze mechanism. Freeze:

- commit SHA;
- manifest hash;
- exact implementation names/versions;
- parameters;
- model/checkpoint hashes if any;
- dependency/environment versions;
- research sidecar version;
- chosen confidence/loss rule.

After freeze, no tuning from held-out evidence.

## 13. Phase 7 — #57 held-out evaluation

Run each frozen candidate once on the held-out real-video set according to #57.

Required report additions, in addition to #57's normative gates:

- research delta-position metrics;
- de-biased trajectory diagnostic;
- condition-stratified jitter/drift;
- comparison with current SAM circle and CSRT baselines.

Possible outcomes:

1. **SELECT:** one candidate satisfies the held-out tracking decision and is viable for later
   production integration;
2. **HYBRID SELECT:** the evidence justifies a specific coarse + precision/fusion architecture;
3. **REJECT:** current candidates still do not justify production selection.

Do not revise the 3 px gate inside this experiment. If evidence shows the gate is below annotation
repeatability or poorly aligned with kinematic usefulness, open a separate validation decision that
preserves the original target and rationale.

## 14. Phase 8 — physical/kinematic reference and phone runtime

Tracker selection alone does not prove VBT accuracy.

### P8.1 — independent reference

Use docs/validation/M0_REFERENCE_STUDY.md with a synchronized encoder/LPT, controlled geometric rig
or independently calibrated reference system.

The study must definition-match:

- coordinate axis/sign;
- exact interval;
- calibration;
- filter;
- mean velocity definition;
- peak velocity definition;
- timestamp mapping and uncertainty.

The existing filmed stick and WL Analysis agreement may remain secondary development/comparison
evidence, not substitutes for independent physical ground truth.

### P8.2 — Pixel 8 runtime

After a candidate is frozen and the recording envelope is sufficiently defined, run the existing
#59/ADR-0009 phone benchmark on the Google Pixel 8.

Measure the full frozen path required for the candidate, not an isolated model microbenchmark.

## 15. Recommended implementation order

The next work should be small PRs, not one large implementation PR.

1. **Metric harness PR**
   - development-only delta/de-biased/jitter diagnostics;
   - synthetic sanity tests.
2. **Controlled marker PR**
   - capture protocol;
   - simple reference tracker;
   - same-video comparison.
3. **Absolute metrology PR**
   - radial edge + robust geometry;
   - optional radial symmetry candidate.
4. **Relative registration PR**
   - phase/local DFT registration;
   - direct delta scoring.
5. **Fusion PR**
   - deterministic global fit;
   - standard prediction output.
6. **Conditional learned-localizer PR**
   - only if coarse localization is proven to be the remaining bottleneck.
7. **Freeze/held-out PR**
   - #57 one-shot evaluation.
8. **Independent-reference/runtime PRs**
   - M0 kinematic and phone gates.

## 16. Likely files/modules

Research-only initial work:

- research/opencv-tracking/compare.py or a dedicated research evaluator;
- research/plate-metrology/ (new, only when implementation starts);
- research/marker-reference/ (new, only when implementation starts);
- target/ for generated non-private reports;
- validation/private/ for private media/frames/labels.

Production/core changes should be avoided until promotion. If a winning research candidate later
needs a reusable deterministic mathematical primitive, move the minimum generic part into
openbar-core only through normal architecture review.

## 17. Verification for implementation PRs

Every implementation PR must run the applicable repository checks from AGENTS.md:

    cargo fmt --all -- --check
    cargo clippy --locked --workspace --all-targets --all-features -- -D warnings
    cargo build --locked --workspace --all-targets --all-features
    cargo test --locked --workspace --all-targets --all-features
    python -m unittest discover -v -s validation/tests -p 'test_*.py'
    python validation/tools/schema_check.py

Research Python dependencies should stay in isolated research environments. A new runtime
dependency requires source/licence/purpose/distribution review and cargo-deny compatibility where
applicable.

## 18. Evidence artifacts to retain

For each research candidate retain:

- implementation name/version/config;
- input fixture/manifest hashes;
- environment/library/model provenance;
- prediction SHA-256;
- diagnostics sidecar SHA-256;
- exact commands;
- per-clip metric tables;
- paired baseline comparisons;
- representative failure descriptions;
- runtime/memory;
- run-to-run difference where nondeterministic hardware may matter.

Do not commit private media, extracted private frames, private labels, model checkpoints without
redistribution rights, or private coordinates whose publication has not been approved.

## 19. Explicit decision table

| Evidence outcome | Action |
| --- | --- |
| Marker is much cleaner; natural tracker noisy | prioritize plate metrology/localization |
| Marker and natural tracker have similar velocity error | prioritize timing/exposure/filter/reference investigation |
| Absolute geometry wins; relative registration adds little | keep simpler absolute candidate |
| Relative registration improves delta error but drifts | combine with absolute anchors |
| Fusion improves centre but attenuates/warps peaks | reject fusion formulation |
| Coarse localization is reliable | do not add YOLO/detector |
| Coarse localization is the limiting failure | run conditional learned-localizer phase |
| YOLO helps but licensing is incompatible | retain evidence, find permissive replacement or obtain separate licence |
| Held-out candidate fails | reject/iterate on development; do not retune on held-out |
| Tracking passes but physical velocity fails | investigate calibration/timing/filtering; do not blame tracker automatically |
| Physical reference passes but Pixel 8 runtime fails | optimize implementation/runtime without weakening accuracy |

## 20. Promotion boundary

Nothing in this plan becomes the default M0 pipeline automatically.

A production promotion requires:

- held-out #57 evidence;
- independent physical/kinematic evidence where the claim depends on kinematics;
- supported recording-envelope evidence;
- Pixel 8 runtime evidence;
- dependency/licence review;
- a separate ADR if the durable architecture changes;
- schema/version review if persisted semantics change.

Until then, the current authoritative M0 contracts and scope remain unchanged.

## 21. First implementation and development decision (2026-10-05)

The research implementation and commands are in
[`research/bar-path-measurement/`](../../research/bar-path-measurement/README.md).
The [first development report](../analysis/M0_BAR_PATH_MEASUREMENT_DEVELOPMENT_RESULTS.md) records
the three-clip baseline comparisons, bounded circle grid, relative-registration and fusion evidence,
and phase-specific pending inputs.

The current complete candidates are not eligible for freeze: geometry loses too much squat/clean
evidence, despite useful partial snatch and relative-displacement results. Marker capture, dense
development repeatability, independent reference and Pixel 8 measurements remain pending. No
held-out fixture was processed and no production default, schema or gate changed. The learned
localizer trigger was not established, so that conditional phase did not start.
