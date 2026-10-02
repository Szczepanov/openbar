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

### openbar-tracking

M0 Rust tracking experiments behind a decoder-agnostic boundary.

The crate accepts display-oriented grayscale image access, authoritative timestamps, and the
canonical manual seed from `openbar-core`. It owns experimental tracker implementations,
explicit tracked/low-confidence/lost state, and adapters into the common benchmark prediction contract.

It deliberately does **not** own codecs, media paths, camera APIs, Flutter types, or nominal-FPS
measurement logic. A future media layer may adapt decoded buffers into this boundary without
moving domain logic into UI/media code. Tracker confidence semantics remain implementation-specific
rather than being treated as calibrated across algorithms.

ADR-0005 records this M0 boundary. The first implementations are experimental baselines, not a
production tracker selection.

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

## Candidate future measurement protocol boundary (post-M0; non-normative)

This section records a **design hypothesis**, not an accepted architecture decision and not authorization to implement post-M0 scope. The current barbell M0 implementation, accepted ADRs, and acceptance criteria remain normative.

If a second measurement family is implemented and validated, use its concrete evidence/events/results to decide through an ADR whether a protocol-oriented boundary is justified. One candidate boundary is:

```text
media / decoded frames / authoritative timestamps
                     |
              evidence producers
        +------------+-------------+
        |            |             |
   lift tracker   event marker   future pose /
                                gate detection
        |            |             |
        +------------+-------------+
                     |
              canonical evidence
                     |
             MeasurementProtocol
        +------------+-------------+
        |            |             |
   LiftProtocol  JumpProtocol  SprintProtocol
        |            |             |
        +------------+-------------+
                     |
         typed protocol result
       + events + metrics + quality
       + confidence + provenance
```

`MeasurementProtocol` is a **candidate future domain abstraction**, not an M0 trait/API requirement. If adopted by a later ADR, its job
is to represent measurement semantics independently from the mechanism that produced the evidence.

A future protocol implementation should be responsible for:

- identifying its protocol and implementation version;
- declaring/validating the evidence and calibration it requires;
- validating recording/support-envelope assumptions relevant to that protocol;
- deriving protocol events and metrics deterministically from retained evidence;
- preserving event/metric uncertainty and explicit failure;
- retaining enough provenance to reproduce the result.

It should **not** own:

- camera/video decoding;
- Flutter/UI state;
- a particular CV or ML framework;
- model training;
- cloud/backend concerns.

The abstraction must not assume that every protocol has a tracker or continuous trajectory.
Canonical evidence may include:

- timestamped position observations;
- manually or automatically marked timestamped events;
- calibrated points/lines/planes;
- later pose/landmark observations;
- protocol-specific reference geometry.

This distinction is important because the first jump and sprint implementations can be event-based
without pose estimation or continuous body tracking.

### Candidate future protocol modules

The hypothesized measurement-family boundaries are:

#### LiftProtocol

The lift protocol is the future home of the existing barbell measurement semantics:

- plate/bar target evidence;
- plate-diameter calibration;
- trajectory/filtering/kinematics;
- rep/event interpretation when validated;
- lift-specific metrics and comparisons.

**M0 is not being refactored into this module now.** The current `openbar-core`,
`openbar-tracking`, `analysis::Analysis`, manual seed, and plate calibration remain the
authoritative M0 implementation. Extract a dedicated lift protocol only when doing so is justified
by a real second protocol rather than speculative abstraction.

#### JumpProtocol

Tracked by #68.

The first jump implementation should be able to work without pose estimation:

- vertical jump: manually marked or independently detected take-off/landing timestamps, with
  flight-time-derived height and explicit temporal uncertainty;
- broad jump: calibrated ground reference plus take-off/landing points and an explicit distance
  protocol.

Pose/landmark inference may later automate event/point detection, but it is an evidence producer,
not a requirement of the protocol abstraction.

#### SprintProtocol

Tracked by #69.

The first sprint implementation should support known-distance gate/split timing:

- measured gate/reference geometry;
- explicit start/crossing definition;
- timestamped gate-crossing events;
- split time and distance/time speed metrics.

Continuous speed can be added later using a calibrated body trajectory (potentially from pose
estimation), without changing the protocol boundary.

### Candidate future crate/module direction

If implementation pressure justifies separate crates, the likely direction is:

```text
crates/
  openbar-core/              # shared deterministic measurement primitives
  openbar-tracking/          # current M0 tracking
  openbar-video/             # future media boundary
  openbar-inference/         # future production inference
  openbar-protocol-lift/     # future
  openbar-protocol-jump/     # future (#68)
  openbar-protocol-sprint/   # future (#69)
```

These crate names are directional, not commitments. Prefer starting with the smallest module
boundary that preserves clean dependencies; extract crates only when they provide a concrete
build/test/dependency benefit.

### M0 scope fence

This future architecture does **not** change `docs/roadmap/M0.md`.

In particular, M0 still does not require:

- a `MeasurementProtocol` implementation;
- jump or sprint code;
- generic calibration replacing `PlateDiameterCalibration`;
- a generic event schema;
- pose estimation;
- automatic detection;
- live camera mode;
- Flutter integration.

Any implementation that changes the canonical persisted analysis shape, calibration semantics, or
accepted M0 measurement behavior requires the normal schema/ADR review rather than being justified
by this future direction alone. Until such an ADR is accepted, this candidate boundary must not be
treated as a dependency rule, crate requirement, or persistence contract.

## Current lift frame pipeline target

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

The first M0 tracker baselines intentionally add no production CV dependency. A future external CV
dependency must record source, licence, purpose, distribution compatibility, and native/system
implications before adoption.

## Reproducibility

Every persisted analysis should eventually include:

- source media identity/metadata;
- protocol identity/version once protocol modules exist;
- calibration method and parameters where the protocol requires calibration;
- retained raw measurement evidence (a trajectory for current lift analysis; potentially timed
  events/points/other evidence for future protocols);
- derived/filtered data or sufficient parameters to reproduce it;
- event definitions and uncertainty where events are measured;
- model name/version if inference was used;
- pipeline/protocol implementation version;
- confidence/quality flags;
- timestamps in a single well-defined time base.

The current M0 schema remains trajectory-centric and unchanged. Future protocol persistence must be
introduced through explicit schema/versioning work rather than by silently weakening M0 invariants.
