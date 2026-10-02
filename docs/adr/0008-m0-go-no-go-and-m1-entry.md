# ADR-0008: M0 no-go/rework decision and M1 entry criteria

- Status: Accepted
- Date: 2026-10-02
- Issue: #16
- Evaluated commit: `b94d09c4b451d989d17926a7df9ca8a3c347337f`

## Context

M0 exists to answer one product/architecture question before OpenBar moves up-stack:

> Does ordinary phone video support sufficiently accurate, reproducible, failure-aware bar-path
> measurement inside a useful recording envelope to justify automatic detection and product UI work?

The implementation work needed to ask that question now exists: manual-seed tracking baselines,
plate-diameter calibration, canonical analysis/provenance, filtering experiments, timestamp-authoritative
kinematics, FFmpeg-backed real-video decode, a headless CLI, diagnostic rendering, reproducible evidence
generation, and an executable recording-support policy.

The evidence package also makes the current limitation explicit: most product-level M0 gates are not
yet measurable from held-out, annotated, non-synthetic evidence. M0 must therefore end with a decision
rather than treating completed implementation tasks as evidence that the product hypothesis passed.

## Evidence frozen for this review

This decision evaluates the repository state at the commit above and the following durable evidence:

| Area | Evidence |
| --- | --- |
| Reproducible evidence package | #14; `docs/validation/M0_EVIDENCE_REPORT.md` |
| Recording envelope | #15; `docs/validation/RECORDING_ENVELOPE.md` |
| Tracker experiments | #7; `docs/validation/TRACKER_EXPERIMENTS.md` |
| Calibration | #8; `docs/validation/PLATE_CALIBRATION.md` |
| Filter experiments | #10; `docs/validation/FILTER_EXPERIMENTS.md` |
| Kinematic definitions | #11; `docs/validation/KINEMATIC_METRICS.md` |
| End-to-end CLI | #12; `docs/validation/CLI_PIPELINE.md` |
| Diagnostics/failure rendering | #13; `docs/validation/DIAGNOSTIC_RENDERING.md` |
| Runtime/language boundary | ADR-0001 |
| Canonical analysis model | ADR-0003 |
| Validation-first M0 | ADR-0004 |
| Tracker frame boundary | ADR-0005 |
| M0 video decode boundary | ADR-0006 |
| Pixel coordinate convention | ADR-0007 |

The public reproducible evidence set contains one synthetic development fixture: clean, 12 fps,
320x240, declared side view, fixed camera, ten comparable labelled samples. Private development
evidence includes real snatch clips, but it is not a held-out annotated accuracy set.

No production tracker or filter is selected by the evidence package.

## Provisional gate table

The original M0 engineering targets remain the reference. A target is not silently weakened because
the current dataset cannot measure it.

| Gate | Target | Review status | Reason |
| --- | ---: | --- | --- |
| Plate-centre tracking MAE | < 3 px | **NOT MEASURABLE YET** | Synthetic development results exist, but there is no annotated held-out non-synthetic validation set and no selected production tracker. |
| Tracking availability in supported clips | > 99% | **NOT MEASURABLE YET** | No selected production tracker, no held-out real accuracy set, and no real-world condition is currently classified fully supported. |
| Range-of-motion MAE | < 0.01 m | **NOT MEASURABLE YET** | No independent calibrated physical ROM reference exists. |
| Mean velocity MAE | < 0.05 m/s | **NOT MEASURABLE YET** | No definition-matched physical/reference velocity source exists. |
| Peak velocity MAE | < 0.10 m/s | **NOT MEASURABLE YET** | No definition-matched physical/reference velocity source exists. |
| Repeat-analysis determinism | 100% | **PASS** | Repeated tracker, benchmark and canonical analysis outputs agree for the evaluated public subset; canonical analysis also repeats on the available local real development run. |
| Offline processing | faster than video duration on reference hardware | **NOT MEASURABLE YET** | Desktop timings are diagnostic only; phone-class reference hardware/runtime has not been designated. |

There are no evidence-backed FAIL or REVISED gate outcomes in this review. Five accuracy/usefulness
gates and the mobile-target performance gate remain unmeasured.

## Supported recording envelope

The current recording-support policy correctly avoids manufacturing numeric limits from sparse data.

Current state:

- declared side view + fixed camera: **warning**, not fully supported;
- oblique_45, front, rear, and unknown view: **unsupported**;
- handheld, panning, moving-other, and unknown camera movement: **unsupported**;
- controlled yaw, pitch/height, distance/framing, FPS, resolution, blur, compression, plate size,
  contrast, and occlusion-duration boundaries: **unknown**;
- there is deliberately no fully supported real-world recording condition yet.

This policy is a safety success, but it is also a product-evidence blocker. A warning-only real-world
envelope is not enough to claim that the M0 measurement approach is useful for ordinary athlete/coach
use.

## Review findings

### Measurement usefulness

**Not established.**

The synthetic local-contrast baseline measures well on the single development fixture, while the
template baseline performs materially worse there. Neither result can be generalized to ordinary
phone video.

Real development observations expose a more important risk: the template tracker can remain
high-confidence after following the wrong target. Confidence and availability therefore cannot
substitute for annotated position error.

ROM and velocity implementations have deterministic semantics, but their real measurement error is
not yet known.

### Failure safety

**Architecturally strong, with one evidence-critical tracker risk.**

The system already does the following correctly:

- preserves raw observations separately from derived values;
- represents tracker loss explicitly;
- does not silently interpolate long gaps;
- prevents kinematic derivatives across configured unsupported gaps;
- propagates confidence/quality state;
- rejects non-side or moving-camera geometry before physical analysis is emitted;
- leaves side/fixed real-world footage warning-only while evidence is incomplete;
- renders loss/low-confidence diagnostic state rather than hiding it.

The unresolved risk is silent false tracking: a tracker may be confidently wrong. That cannot be
solved by presentation or confidence propagation alone; it requires held-out annotated evidence and,
if necessary, a different/hybrid tracker.

### Reproducibility

**Pass for the evaluated scope.**

The public evidence package is reproducible from documented commands. Identical input/configuration
produces deterministic normalized tracker/benchmark outputs and byte-identical canonical analysis for
the evaluated path.

Canonical output records tracker/filter/calibration/kinematics provenance. Decoder provenance is
recorded for real-video runs.

This is a reproducibility result, not an accuracy result.

### Architecture

**Pass for M0 boundaries; no architecture rewrite is required before rework.**

- Rust remains the authoritative production measurement implementation.
- `openbar-core` remains independent of Flutter and media/codecs.
- `openbar-tracking` is decoder-agnostic and consumes display-oriented frames plus authoritative
  timestamps and the canonical seed contract.
- Media/decode work remains in the CLI boundary for M0.
- Canonical analysis keeps raw, calibrated, filtered, kinematic, confidence and provenance layers
  separate.
- Future automatic detection can propose the existing target-seed semantics rather than redefining
  calibration/trajectory/kinematics.

The principal architecture caveat is ADR-0006: external FFmpeg is suitable for the headless M0
harness, but it is still a proposed M0 decode choice and is not a mobile-runtime commitment.

### Performance

**Not established for the intended mobile target.**

Desktop decode/tracker timings exist, including evidence that exhaustive template search becomes very
slow at realistic search radii. The M0 gate explicitly targets phone-class reference hardware, which
has not been defined or measured.

### Evidence quality

**Insufficient for M1 entry.**

The current package is valuable because it prevents false confidence:

- one synthetic development fixture is not a validation dataset;
- private real clips are development observations, not held-out accuracy evidence;
- there is no independent physical ROM/velocity reference;
- there is no phone-class performance result;
- most recording-envelope boundaries remain unknown.

The missing evidence is concentrated in solvable validation/data/runtime work rather than a
demonstrated fundamental impossibility, but that is a reason to rework M0, not a reason to skip it.

## Unresolved risks and disposition

| Risk | Category | Disposition |
| --- | --- | --- |
| Tracker may remain confidently locked to the wrong object | Technical / measurement | **Decision blocker** — #57 |
| No production tracker/filter selected from held-out real evidence | Technical / evidence | **Decision blocker** — #57 |
| No fully supported real-world recording condition | Product / evidence | **Decision blocker** — #53 after #57 provides a frozen candidate |
| ROM/mean/peak velocity accuracy is unmeasured against an independent physical reference | Measurement / evidence | **Decision blocker** — #58 |
| Phone-class offline runtime is unmeasured | Runtime / product | **Decision blocker** for mobile integration — #59 |
| Current public validation dataset is too small and synthetic | Data | **Decision blocker** — #57 and #53 |
| ADR-0006 external-FFmpeg boundary is not a mobile distribution design | Architecture / runtime | **Accepted for headless M0 only**; revisit through #59 before mobile integration |
| HEVC/B-frame/HDR/non-zero-start and broader decode coverage remain incomplete | Technical / compatibility | **Accepted for rework evidence only** when outside studied fixtures; must not be claimed supported without evidence |
| Licence/trademark/privacy/app-store/commercial-launch review remains outstanding | Legal / launch | **Out of scope for this technical decision**; still blocks public commercial launch where applicable |

## Decision

**NO-GO / REWORK.**

The current evidence does **not** justify entering M1, implementing automatic plate detection as a
production milestone, or starting Flutter/mobile measurement integration.

This is not a rejection of the architecture. The implementation is sufficiently modular and
reproducible to continue evidence generation without an architectural reset. The no-go is caused by
missing product-level measurement evidence.

M0 is considered a completed decision milestone once this ADR is merged: it successfully produced an
evidence-based negative entry decision. Closing the M0 umbrella does not mean the measurement
hypothesis passed.

## Required rework

The following issues form the re-entry path:

1. **#57 — Select tracker/filter using held-out real-video evidence**
   - establish the plate-centre MAE and availability gate outcomes;
   - select or reject a production tracker/filter candidate;
   - explicitly evaluate silent false-track behaviour.
2. **#53 — Quantify recording-envelope boundaries with held-out real evidence**
   - use the frozen candidate from #57;
   - promote at least one practically useful real side/fixed condition from warning to supported;
   - keep unmeasured dimensions unknown.
3. **#58 — Validate ROM and velocity against independent physical reference**
   - assign PASS / FAIL / REVISED status to ROM, mean velocity and peak velocity.
4. **#59 — Define phone-class reference hardware and measure offline runtime**
   - assign PASS / FAIL / REVISED status to the offline-processing gate;
   - identify any decoder/streaming/mobile architecture follow-up before Flutter integration.

## M1 entry criteria

M1 remains locked until a new review records all of the following:

1. A production tracker/filter candidate is selected from held-out annotated non-synthetic evidence,
   or the current candidates are rejected and a replacement is validated.
2. Plate-centre MAE and tracking availability have evidence-backed **PASS** or explicitly justified
   **REVISED** outcomes inside a defined envelope.
3. At least one practically useful real-world side/fixed recording envelope is classified
   **supported**, not warning-only.
4. ROM, mean velocity and peak velocity have evidence-backed **PASS** or explicitly justified
   **REVISED** outcomes against independent definition-matched references, unless a later ADR
   deliberately removes one of those outputs from the M1 product scope.
5. Repeat-analysis determinism continues to pass for the frozen candidate and expanded evidence set.
6. Offline processing has an evidence-backed **PASS** or explicitly justified **REVISED** outcome on
   defined phone-class reference hardware/runtime.
7. No blocker-level measurement defect remains open for the intended first M1 recording envelope.
8. The re-review explicitly records **GO** or **CONDITIONAL GO** and updates this decision.

A revised target is acceptable only when evidence demonstrates that the original engineering target
was inappropriate for the intended construct/use case. “We cannot currently meet/measure it” is not
sufficient rationale for revision.

## Scope forbidden before re-entry

Until the re-entry review records GO or CONDITIONAL GO:

- do not make automatic plate detection a production milestone;
- do not build detector-tracker integration as the authoritative measurement path;
- do not start Flutter/mobile measurement UI as the main integration surface;
- do not make public accuracy claims from the current synthetic/development evidence;
- do not relax warning/unsupported recording-support rules merely to make more footage analyzable;
- do not silently select a tracker/filter based on overlay appearance or development-only results.

Isolated research spikes are acceptable only when they directly support the rework issues and remain
clearly non-production.

## Consequences

### Positive

- The project preserves the validation-first contract instead of treating implementation completion
  as product validation.
- Existing architecture remains usable for the required rework.
- The missing evidence has explicit owners/issues and gate linkage.
- M1 cannot accidentally inherit unvalidated tracker/filter choices or fabricated recording limits.

### Cost

- Automatic detection and Flutter work are delayed.
- More real-data annotation/reference collection is required.
- Independent physical reference and phone-class runtime testing add effort before product-layer work.

Those costs are intentional: they are lower than building product layers on an unvalidated
measurement core.

## Revisit

Revisit this ADR when #57, #53, #58 and #59 provide enough evidence for a new M1 entry review.
