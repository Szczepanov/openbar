# Architecture

## Decision summary

OpenBar is designed around a reusable, headless measurement engine.

```text
                    future Flutter UI
                          |
                          | FFI
                          v
+------------------------------------------------+
|                  Rust production core          |
| calibration | trajectory | filtering |         |
| kinematics  | confidence | events | comparison |
+--------------------------+---------------------+
                           |
               +-----------+-----------+
               |                       |
               v                       v
       production inference      native media APIs
          (ONNX Runtime)       (decode/camera/GPU)
               ^
               |
            model.onnx
               ^
               |
       Python / PyTorch research
```

## Boundaries

### openbar-core

Authoritative deterministic domain logic:

- calibration;
- trajectory representation;
- filtering interfaces/implementations;
- kinematics;
- confidence/provenance;
- rep/event state logic once introduced;
- comparison metrics once introduced.

It should not depend on Flutter or a specific mobile platform.

### openbar-inference (future crate)

Production preprocessing/inference orchestration. Intended to load exported ONNX models. Model training does not live here.

### openbar-video (future crate/platform layer)

Frame/timestamp abstractions and video adapters. Hardware-accelerated native APIs are preferred where they reduce copies and power use.

### Python ML workspace (future)

Research-only environment for:

- dataset preparation;
- augmentation;
- detector/tracker experiments;
- model training;
- evaluation;
- export to ONNX;
- scientific notebooks.

Python is not the authoritative production implementation of calibration/kinematics.

### UI (future)

Flutter is the preferred cross-platform application layer after M0. The UI consumes structured analysis results; it does not reimplement measurement algorithms.

## Frame pipeline target

```text
decode/camera frame
 -> crop/resize
 -> detector periodically
 -> high-rate tracker between detections
 -> trajectory samples + confidence
 -> outlier handling/filter
 -> calibration
 -> kinematics
 -> rep/events
 -> UI/export
```

The intended real-time design may run detection at a lower frequency than tracking.

## Data movement

Avoid passing full decoded frames repeatedly across FFI boundaries. The eventual native/Rust integration should keep frame buffers close to processing and return small structured results to the UI.

## Dependency policy

Prefer small, explicit dependencies. OpenCV may be used where it clearly saves substantial CV implementation effort, but it should not become an unquestioned dependency for the whole system. Model inference should be abstracted so ONNX Runtime can be evaluated without coupling domain logic to it.

## Reproducibility

Every persisted analysis should eventually include:

- source media identity/metadata;
- calibration method and parameters;
- raw trajectory;
- filtered trajectory or sufficient parameters to reproduce it;
- model name/version if inference was used;
- pipeline version;
- confidence/quality flags;
- timestamps in a single well-defined time base.
