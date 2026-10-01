# M0 Evidence Report — Public Reproducible Subset

Issue: #14

This is an engineering evidence report, not a scientific or marketing accuracy claim.
The current redistribution-safe dataset contains one synthetic development fixture and no
held-out real lifting footage. Tracker measurements below are useful for pipeline regression
and failure inspection, but they are not sufficient to establish product-level accuracy gates.

## Evaluated configuration

- Trackers: template-sad-v1 and local-contrast-centroid-v1, both retained as M0 baselines.
- Canonical integration probe: template tracker + raw-identity@1 filter.
- Plate-diameter calibration input: 0.45 m.
- Kinematics: timestamp-authoritative backward difference, max continuity gap 0.2 s.
- Benchmark confidence threshold: 0.0; tracker-declared loss remains explicit.

No production tracker or filter winner is selected by this report.

## Dataset composition

- Public fixtures: 1.
- Development fixtures: 1.
- Held-out validation fixtures: 0.
- Comparable labelled samples in the public fixture: 10.
- Exercise coverage: clean only; snatch and back squat are absent.
- FPS coverage: 12 fps only.
- Camera: fixed side view only; camera movement and yaw are not represented.
- Motion blur: none.
- Occlusion: partial and full occlusion states exist, but the fully occluded frame is intentionally unlabelable and excluded from coordinate-error denominators.

Annotation repeatability on the synthetic fixture is reported separately from tracker error:
mean Euclidean disagreement 0.9 px, RMSE 0.948683 px, maximum 1.0 px over 10 matched labels.

## Tracker results

| Tracker | n tracked/comparable | Availability | X MAE/RMSE px | Y MAE/RMSE px | Centre MAE/RMSE px | p50 / p90 / p95 / max px | Max loss |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| template-sad-v1@1 | 10/10 | 100.000% | 1.100 / 3.479 | 8.800 / 12.954 | 9.256 / 13.413 | 4.000 / 22.000 / 26.000 / 26.000 | 0 samples |
| local-contrast-centroid-v1@1 | 10/10 | 100.000% | 0.748 / 0.821 | 0.833 / 1.285 | 1.303 / 1.525 | 1.095 / 2.264 / 2.921 / 2.921 | 0 samples |

These values are from the single synthetic development fixture. They must not be generalized
to ordinary phone video or the supported-condition envelope.

## Provisional gate status

| Gate | Target | Status | Evidence/rationale |
| --- | ---: | --- | --- |
| Plate-centre tracking MAE | < 3 px | **NOT MEASURABLE YET** | The public run reports exact synthetic-fixture MAE for both retained tracker baselines. Only one synthetic development fixture exists; there is no held-out real-video sample. |
| Tracking availability in supported clips | > 99% | **NOT MEASURABLE YET** | Availability/loss is measured on the public synthetic development fixture. The supported real recording envelope and held-out validation set do not yet exist. |
| Range-of-motion MAE | < 0.01 m | **NOT MEASURABLE YET** | ROM semantics and deterministic synthetic implementation tests exist. The public subset has no independent calibrated physical ROM reference. |
| Mean velocity MAE | < 0.05 m/s | **NOT MEASURABLE YET** | Mean-axis velocity semantics are implemented for explicit intervals. There is no definition-matched physical/reference velocity dataset in the public subset. |
| Peak velocity MAE | < 0.10 m/s | **NOT MEASURABLE YET** | Peak signed-axis velocity semantics are implemented for explicit intervals. There is no definition-matched physical/reference velocity dataset in the public subset. |
| Repeat-analysis determinism | 100% | **PASS** | Two tracker runs, repeated benchmark evaluation, and two canonical analyze runs were compared. PASS is scoped to the current public synthetic subset and frozen configuration. |
| Offline processing | faster than video duration on reference hardware | **NOT MEASURABLE YET** | Per-tracker wall-clock runtime and media/runtime ratio are recorded in this evidence artifact. No stable project reference-hardware target is designated, and the one-second fixture is not representative. |

## Determinism and performance

- Normalized tracker prediction streams identical across two complete tracker runs: True.
- Benchmark JSON identical across repeated evaluation of the same prediction streams: True.
- Canonical analysis JSON byte-identical across two end-to-end runs: True.
- Runtime is recorded per tracker in the machine-readable evidence, but the offline-speed gate remains
  NOT MEASURABLE YET: the project has not designated stable reference hardware and a one-second
  synthetic clip is not representative of the M0 recording envelope.

## Failure cases and unsupported conditions

- Benchmark loss is never converted into zero coordinate error.
- The public fixture contains one fully occluded, deliberately unlabelable frame; it is excluded from
  coordinate metrics rather than filled or interpolated.
- Diagnostic SVG artifacts are generated for the public analysis and a committed loss/low-confidence
  canonical-analysis fixture so the rendering path exposes gaps instead of hiding them.
- No public evidence currently supports conclusions for realistic motion blur, camera motion, distance
  variation, yaw, gym clutter, plate/background contrast variation, snatch, back squat, or held-out data.

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

1. Add redistribution-safe or locally reproducible real clean/snatch/back-squat fixtures with held-out roles.
2. Add condition coverage for realistic frame rates, distance/framing, blur, camera movement, contrast and yaw.
3. Add independent calibrated position/ROM reference and definition-matched velocity reference.
4. Re-run this package without changing gate semantics; only then promote tracker/kinematic gates from
   NOT MEASURABLE YET to evidence-backed PASS/FAIL or justify a target revision.
