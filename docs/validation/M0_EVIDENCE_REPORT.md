# M0 Evidence Report — Public Reproducible Subset

Issue: #14

This is an engineering evidence report, not a scientific or marketing accuracy claim.
Tracker measurements below come from redistribution-safe fixtures only. They are useful for
pipeline regression and failure inspection, but they are not sufficient to establish
product-level accuracy gates.

## Evaluated configuration

- Trackers: template-sad-v1 and local-contrast-centroid-v1, both retained as M0 baselines.
- Canonical integration probe: template tracker + raw-identity@1 filter.
- Plate-diameter calibration input: 0.45 m.
- Kinematics: timestamp-authoritative backward difference, max continuity gap 0.2 s.
- Benchmark confidence threshold: 0.0; tracker-declared loss remains explicit.

No production tracker or filter winner is selected by this report.

## Dataset composition

- Fixtures: 1 (1 synthetic, 0 non-synthetic).
- Development / held-out validation fixtures: 1 / 0.
- Annotated fixtures: 1; annotated held-out non-synthetic: 0.
- Comparable labelled samples: 10.
- Exercises: clean; absent: snatch, back_squat.
- Nominal FPS: 12.
- Camera view / movement: side / fixed.
- Motion blur: none; occlusion: moderate; lighting: good.

Annotation repeatability on the synthetic fixture is reported separately from tracker error:
mean Euclidean disagreement 0.9 px, RMSE 0.948683 px, maximum 1.0 px over 10 matched labels.

## Tracker results

| Tracker | n tracked/comparable | Availability | X MAE/RMSE px | Y MAE/RMSE px | Centre MAE/RMSE px | p50 / p90 / p95 / max px | Max loss |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| template-sad-v1@1 | 10/10 | 100.000% | 1.100 / 3.479 | 8.800 / 12.954 | 9.256 / 13.413 | 4.000 / 22.000 / 26.000 / 26.000 | 0 samples |
| local-contrast-centroid-v1@1 | 10/10 | 100.000% | 0.748 / 0.821 | 0.833 / 1.285 | 1.303 / 1.525 | 1.095 / 2.264 / 2.921 / 2.921 | 0 samples |

These values are from synthetic development material. They must not be generalized
to ordinary phone video or the supported-condition envelope.

## Provisional gate status

| Gate | Target | Status | Evidence/rationale |
| --- | ---: | --- | --- |
| Plate-centre tracking MAE | < 3 px | **NOT MEASURABLE YET** | 10 comparable labelled samples exist across evaluated fixtures; per-tracker MAE is reported per fixture. No annotated held-out non-synthetic fixture exists; no production tracker is selected (#16). |
| Tracking availability in supported clips | > 99% | **NOT MEASURABLE YET** | Availability/loss is reported per tracker and fixture. No annotated held-out non-synthetic fixture exists; no production tracker is selected (#16). The supported recording envelope (#15) is not defined yet. |
| Range-of-motion MAE | < 0.01 m | **NOT MEASURABLE YET** | ROM semantics and deterministic synthetic implementation tests exist. No fixture has an independent calibrated physical ROM reference. |
| Mean velocity MAE | < 0.05 m/s | **NOT MEASURABLE YET** | Mean-axis velocity semantics are implemented for explicit intervals. No fixture has a definition-matched physical/reference velocity source. |
| Peak velocity MAE | < 0.10 m/s | **NOT MEASURABLE YET** | Peak signed-axis velocity semantics are implemented for explicit intervals. No fixture has a definition-matched physical/reference velocity source. |
| Repeat-analysis determinism | 100% | **PASS** | Public subset: repeated tracker runs, benchmark evaluations and canonical analyze runs were compared; local clips: repeated canonical analyze runs and benchmark evaluations. Scoped to the public synthetic subset and the frozen configuration. |
| Offline processing | faster than video duration on reference hardware | **NOT MEASURABLE YET** | Per-tracker wall-clock runtime is recorded in this evidence artifact. The project intends phone-class reference hardware; no specific device is designated and M0 has no mobile build, so desktop timings are diagnostic only. |

## Determinism and performance

- Normalized tracker prediction streams identical across two complete tracker runs: True.
- Benchmark JSON identical across repeated evaluation of the same prediction streams: True.
- Canonical analysis JSON byte-identical across two end-to-end runs: True.
- Runtime is recorded per tracker in the machine-readable evidence. The offline-speed gate remains
  NOT MEASURABLE YET: the project intends phone-class reference hardware, which M0 cannot run yet.

## Failure cases and unsupported conditions

- Benchmark loss is never converted into zero coordinate error.
- The public fixture contains one fully occluded, deliberately unlabelable frame; it is excluded from
  coordinate metrics rather than filled or interpolated.
- Diagnostic SVG artifacts are generated for the public analysis and a committed loss/low-confidence
  canonical-analysis fixture so the rendering path exposes gaps instead of hiding them.
- No public evidence currently supports conclusions for realistic motion blur, camera motion, distance
  variation, yaw, gym clutter, plate/background contrast variation, snatch, back squat, or held-out data.
- Local real-video evidence, when generated, is reported separately in
  [M0_PRIVATE_EVIDENCE_REPORT.md](M0_PRIVATE_EVIDENCE_REPORT.md) as aggregates only.

## Ground truth and threats to validity

- Tracker reference: manual centre digitisation of a first-principles synthetic video.
- Annotation repeatability is measured on two synthetic annotation passes; it is not an estimate of
  annotation noise on real footage.
- There is no independent calibrated physical reference for ROM or velocity in the public subset.
- There is no phase-matched VBT/encoder/reference device material for mean or peak velocity.
- Subgroup claims are intentionally withheld because every requested subgroup other than the single
  clean/12-fps/fixed-camera development condition has zero or tiny support.

## Reproduction

    python3 validation/tools/m0_evidence.py --output-dir target/m0-evidence
    python3 validation/tools/schema_check.py --schema validation/schema/m0-evidence-v1.schema.json target/m0-evidence/m0-evidence-v1.json

The output directory also contains tracker predictions, benchmark JSON, repeated canonical analyses,
filter-experiment evidence, and diagnostic SVGs. Runtime/environment provenance is retained in the JSON.

## Recommended next evidence

1. Annotate the local real snatch clips and add clean/back-squat fixtures with held-out roles.
2. Add condition coverage for realistic frame rates, distance/framing, blur, camera movement, contrast and yaw.
3. Add independent calibrated position/ROM reference and definition-matched velocity reference.
4. Re-run this package without changing gate semantics; only then promote tracker/kinematic gates from
   NOT MEASURABLE YET to evidence-backed PASS/FAIL or justify a target revision.
