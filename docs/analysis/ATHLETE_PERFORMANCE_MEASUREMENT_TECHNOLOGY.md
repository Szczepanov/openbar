# Athlete Performance Measurement Technology Analysis

## Status

**Analysis / recommendation document. Not an accepted ADR.**

External technology and licensing statements in this document are a point-in-time research snapshot reviewed on **2026-10-02**. They must be rechecked before adoption. A framework/repository licence does not, by itself, establish redistribution or commercial rights for downloaded model weights, datasets, or other assets.

This document explores how OpenBar could evolve from a validated bar-path measurement engine into a broader local-first athlete performance measurement platform covering:

- barbell path, velocity, and eventually acceleration;
- vertical jump;
- broad jump;
- short sprint timing and split timing;
- later, longer sprint protocols such as 100 m.

It intentionally does **not** change the current M0 scope. The existing M0 remains focused on proving accurate, reproducible bar-path measurement from ordinary video before adding automatic detection, pose estimation, live camera processing, or a polished mobile application.

Any durable architectural change proposed here should be promoted into an ADR only after a benchmark or implementation spike provides evidence.

---

## Executive recommendation

The recommended long-term technology direction is:

```text
Flutter UI
    |
small structured DTOs / FFI
    |
Rust authoritative measurement engine
    |
    +-- calibration / geometry
    +-- trajectories
    +-- filtering / derivatives
    +-- confidence / provenance
    +-- event detection
    +-- measurement protocols
    |
native media / camera layer
    |
    +-- iOS: AVFoundation
    +-- Android: Camera2 / CameraX
    +-- imported video: FFmpeg / native decoder
    |
vision + inference adapters
    |
    +-- OpenCV where justified
    +-- ONNX Runtime initially
    +-- platform-specialized inference where benchmarks justify it
    |
Python / PyTorch research and model training
```

The core recommendation is **not** to build the application around Ultralytics or OpenCV.

They are tools, not the product architecture.

OpenBar should remain a measurement system where pixels are converted into validated physical quantities through a reproducible pipeline.

The current Rust + Python/PyTorch + ONNX + thin UI architecture is therefore still appropriate. A candidate future evolution is to add protocol-specific measurement semantics for lift, jump, broad-jump, and sprint. The concrete trait/module/crate split should remain uncommitted until a second protocol creates real shared requirements and an ADR accepts the boundary.

---

## 1. Product framing

The broader product can be thought of as:

> a local-first athlete performance measurement platform using ordinary smartphone cameras, with explicit calibration, uncertainty, provenance, reproducibility, and validation.

Potential measurement families:

```text
Strength / lifting
  - bar path
  - range of motion
  - mean velocity
  - peak velocity
  - rep events
  - eventually acceleration

Jump
  - countermovement jump
  - squat jump
  - jump height
  - takeoff velocity
  - flight time
  - eventually RSI / asymmetry where supportable

Horizontal jump
  - standing broad jump
  - takeoff location
  - landing location
  - jump distance

Sprint
  - 5 m
  - 10 m
  - 20 m
  - 30 m
  - 40 m
  - flying splits
  - later longer sprint protocols
```

This is conceptually broader than the current OpenBar product, but the underlying measurement principles remain compatible with the existing foundation.

---

## 2. The main architecture principle: measurement first

The hardest problem is not object detection.

The hard problem is converting video into trustworthy:

- metres;
- seconds;
- metres per second;
- metres per second squared;
- event timestamps;
- distances;
- split times.

A detector may answer:

> where is the athlete or barbell in this frame?

The product must answer:

> what physical quantity was measured, using which calibration, timestamp, model, tracker, filter, assumptions, and confidence?

Therefore the pipeline should remain measurement-centric:

```text
video / camera frames
        |
authoritative timestamps
        |
target observations
        |
calibration / geometry
        |
physical-space trajectory
        |
filtering
        |
kinematics
        |
event detection
        |
protocol-specific metrics
        |
confidence + provenance + export
```

This aligns with the current OpenBar engineering principles:

- raw observations are preserved;
- timestamps are authoritative;
- uncertainty is explicit;
- the same input and pipeline version should reproduce the same output;
- measurement code remains independent from UI.

---

## 3. Recommended technology stack

| Layer | Recommended technology | Role |
| --- | --- | --- |
| Application UI | Flutter | Cross-platform UX, history, sessions, configuration, results |
| iOS capture | Swift + AVFoundation | Camera formats, high-FPS capture, timestamps, buffers |
| Android capture | Kotlin + CameraX / Camera2 | High-speed capture, timestamps, buffers; choose by device evidence and required control |
| Production measurement core | Rust | Calibration, trajectories, filtering, kinematics, events, confidence, protocols |
| Classical CV research | Python + OpenCV | Fast experimentation with tracking, calibration, geometry |
| Production classical CV | Rust/native adapter + selective OpenCV | Only where benchmarked value justifies dependency |
| ML research | Python + PyTorch | Dataset preparation, training, evaluation |
| Pose estimation | RTMPose/MMPose as an initial candidate | Athlete pose / landmark estimation |
| Detection | Benchmark permissively usable models or train custom models | Athlete/plate detection where required |
| Model boundary | ONNX | Portable research-to-production artifact |
| Inference | ONNX Runtime initially | Cross-platform production inference |
| Specialized inference | Core ML / NNAPI / ncnn/etc. when benchmarked | Optional device-specific optimization |
| Calibration markers | ChArUco / ArUco / AprilTag candidates | Ground-plane and camera calibration |
| Local persistence | SQLite + media files + canonical JSON | Local-first session/history storage |
| Validation | Rust/Python headless benchmark harness | Accuracy, repeatability, failure characterization |

### Recommendation

Keep the existing OpenBar language boundaries.

Do **not** move authoritative kinematic or measurement logic into:

- Flutter;
- Python notebooks;
- model outputs;
- platform-specific camera code.

---

## 4. Ultralytics assessment

Ultralytics is technically attractive because it provides convenient workflows for:

- object detection;
- tracking;
- pose estimation;
- dataset tooling;
- model training;
- export.

It is therefore a useful **research benchmark**.

However, it should not currently be a foundational OpenBar dependency.

### Licensing issue

The current Ultralytics repository is licensed under AGPL-3.0, while Ultralytics also offers separate commercial/enterprise licensing for proprietary integration.

That creates an important compatibility problem with OpenBar's current source-available / PolyForm Shield direction.

OpenBar intentionally wants:

- inspectable source;
- optional commercial features later;
- no easy path to a competing commercial fork.

Introducing AGPL code into the application could materially change distribution obligations and should not be done casually.

### Recommendation

Treat Ultralytics as:

- a benchmark candidate in an isolated evaluation environment where its licence obligations are understood;
- an optional commercial dependency only if a future commercial licence is explicitly evaluated and accepted.

Do not commit Ultralytics code/models or derived shipping assets into OpenBar under the current PolyForm Shield distribution posture without an explicit compatibility/legal review.

Do not make the core architecture or persisted model format dependent on Ultralytics.

---

## 5. OpenCV assessment

OpenCV is much better aligned with OpenBar.

OpenCV **4.5.0 and later** use Apache-2.0; OpenCV 4.4.0 and earlier use the 3-clause BSD licence. Current 4.5+ releases provide mature implementations for:

- optical flow;
- feature detection;
- homographies;
- image transforms;
- camera calibration;
- lens distortion correction;
- template matching;
- sub-pixel refinement;
- ArUco / ChArUco calibration;
- tracking-related primitives.

These are directly relevant to OpenBar's measurement problems.

### Recommendation

Use OpenCV aggressively in research and selectively in production.

Avoid allowing OpenCV types to leak into the core domain model.

A possible isolation boundary:

```text
openbar-core
    |
openbar-vision abstraction
    |
    +-- opencv adapter
    +-- native/rust implementation
    +-- future GPU/platform implementation
```

This keeps the measurement model stable if a better implementation is adopted later.

The current architecture already follows this principle by allowing OpenCV where it saves meaningful implementation effort without making it an unquestioned dependency.

---

## 6. Do not force one CV solution onto every measurement

Barbell, jump, broad-jump, and sprint analysis are different measurement problems.

A generic:

```text
YOLO
 -> bounding box
 -> metric
```

pipeline is not sufficient.

Recommended primary approaches:

| Protocol | Primary measurement approach |
| --- | --- |
| Barbell | target tracking + physical calibration |
| Vertical jump | take-off/landing event timing first; optional pose automation later |
| Broad jump | calibrated ground reference + take-off/landing points first; optional landmarks later |
| Sprint | known-distance gate crossing events first; continuous body tracking later |
| 100 m | likely multi-camera or specialized capture/timing architecture |

The reusable components should be:

- timestamp handling;
- calibration interfaces;
- observation representation;
- trajectory representation;
- confidence;
- provenance;
- filtering;
- event infrastructure;
- validation;
- export.

The actual observation generator may differ by protocol.

---

## 7. Future protocol abstraction

The architecture document now records a candidate `MeasurementProtocol` boundary as a non-normative
design hypothesis while explicitly keeping it out of M0 implementation scope.

One hypothesized measurement-family split is:

```text
MeasurementProtocol
    |
    +-- LiftProtocol
    +-- JumpProtocol
    |     +-- vertical jump
    |     +-- broad jump
    |
    +-- SprintProtocol
```

If a later ADR adopts this abstraction, it should consume **protocol evidence** rather than assume a continuous tracker trajectory.
Evidence may include:

- timestamped target observations;
- manually marked events;
- automatically detected events;
- calibrated points/lines/planes;
- later pose/landmark observations.

An adopted protocol implementation should validate its required evidence and support assumptions,
derive typed events and metrics deterministically, and preserve confidence/failure/provenance.

Conceptually:

```text
ProtocolEvidence
    |
    +-- lift trajectory evidence
    +-- jump event/point evidence
    +-- sprint gate/crossing evidence
             |
             v
      MeasurementProtocol
             |
             v
      typed ProtocolResult
```

This avoids coupling the domain model to whichever CV mechanism generated evidence. In particular:

- #68 can start with manual take-off/landing timestamps and manually identified broad-jump points;
- #69 can start with gate-crossing timestamps over known distances;
- pose estimation remains an optional later evidence producer for automation/continuous speed.

Protocol code should not own camera decoding, UI, or ML training.

A possible future crate split is:

```text
crates/
  openbar-core/
  openbar-tracking/
  openbar-vision/                 # future
  openbar-inference/              # future
  openbar-video/                  # future
  openbar-protocol-lift/          # future
  openbar-protocol-jump/          # future (#68)
  openbar-protocol-sprint/        # future (#69)
```

The candidate direction is documented, but the Rust trait/crate extraction remains a design
hypothesis until a second protocol produces concrete shared requirements and an ADR accepts the
boundary. Do not refactor M0 purely to satisfy this future shape.

---

## 8. Barbell measurement

Barbell analysis is the most mature OpenBar use case and should remain M0's focus.

The canonical chain is:

```text
plate / bar target
      |
pixel centre over time
      |
calibration
      |
physical x/y trajectory
      |
filter
      |
velocity
      |
event / rep interpretation
```

### M0 recommendation

Continue with manual target initialization.

Do not block M0 on automatic plate detection.

Benchmark multiple tracking families on the same fixtures, for example:

- pyramidal optical flow;
- feature-based tracking;
- template/correlation tracking;
- detector-assisted tracking;
- learned trackers only if justified.

### Automatic detection later

Once tracking and kinematic accuracy are proven, automatic detection can become a convenience layer:

```text
automatic plate detector
       |
initial target / reacquisition
       |
high-rate tracker
```

Detection does not necessarily need to run on every frame.

---

## 9. Barbell velocity and acceleration

Velocity is already a natural OpenBar output.

Acceleration should remain more conservative.

### Why acceleration is harder

Given position:

```text
x(t)
```

velocity is the first derivative:

```text
v(t) = dx/dt
```

and acceleration is the second derivative:

```text
a(t) = d²x/dt²
```

Differentiation amplifies tracking and timestamp noise.

A small position error can have:

- modest impact on displacement;
- visible impact on velocity;
- very large impact on acceleration.

### Recommendation

Treat outputs in stages:

1. **position** — first-class validated output;
2. **velocity** — first-class only after validation;
3. **acceleration** — experimental until independently validated.

Filtering benchmarks should explicitly evaluate:

- position error;
- velocity error;
- acceleration error;
- peak attenuation;
- latency;
- edge effects;
- event timing changes.

### Avoid false biomechanics claims

Do not report:

```text
barbell mass * measured barbell acceleration
```

as total athlete force.

Likewise, barbell-derived power is not identical to whole-body mechanical power.

Metric names must describe the construct actually measured.

---

## 10. Vertical jump

Issue #68 defines the preferred first implementation as an **event-timing problem**, not a pose
requirement.

A first pipeline can be deliberately simple:

```text
video + authoritative timestamps
          |
manual take-off / landing marks
(or later automatic event detection)
          |
flight time + event uncertainty
          |
jump height
```

This mirrors the successful M0 philosophy: prove the measurement construct with explicit manual
evidence before adding automation.

Pose estimation becomes useful later for:

- automatic take-off/landing proposals;
- takeoff-velocity methods;
- body/COM proxies;
- richer jump mechanics.

It should therefore remain an optional evidence producer rather than a requirement of
`JumpProtocol`.

### Candidate pose technology

RTMPose via MMPose is a strong research candidate because:

- the MMPose framework uses Apache-2.0;
- RTMPose targets real-time pose estimation;
- deployment tooling exists for ONNX and mobile-oriented runtimes.

However:

> framework license does not automatically establish that every pretrained checkpoint and every training dataset is suitable for OpenBar's distribution model.

Every shipped checkpoint must have documented:

- source;
- exact weight version/checksum;
- framework license;
- checkpoint terms;
- training-data provenance where known;
- redistribution/commercial compatibility.

### Jump height algorithms

At least two approaches should be benchmarked.

#### A. Flight-time estimate

If flight duration is `t_f` and ballistic-flight assumptions are satisfied:

```text
h_flight = g * t_f² / 8
```

This quantity should be named and documented as a **flight-time-derived height estimate**: the
ballistic rise from centre-of-mass height at take-off to the flight apex. It is not automatically
equivalent to standing-COM-to-apex displacement or to a force-plate take-off-velocity metric.

Advantages:

- simple;
- no spatial calibration is required for the timing-only construct;
- potentially robust with high-frame-rate capture.

Limitations:

- take-off/landing event precision is critical;
- the method assumes equivalent centre-of-mass height at take-off and landing; landing posture,
  ankle position, or lower-limb flexion can materially bias the estimate;
- frame rate and timestamp quality influence event resolution;
- uncertainty in both event times should be propagated into the reported height uncertainty rather
  than hidden behind a single precise-looking value.

#### B. Takeoff-velocity estimate

Estimate vertical body/COM velocity at takeoff:

```text
h = v_takeoff² / (2g)
```

Advantages:

- can avoid some flight-time assumptions.

Limitations:

- requires more accurate trajectory estimation;
- requires stronger filtering;
- depends on the definition/estimate of body centre of mass;
- differentiation amplifies measurement noise.

### Recommendation

Implement and validate the flight-time/event-timing method first, matching #68. Keep manual event
marking available as a reference path. Then benchmark automatic event detection and the
takeoff-velocity approach against force-plate/reference measurements before promoting either to a
product default.

---

## 11. Broad jump

Broad jump should primarily be treated as a calibrated ground-plane geometry problem.

A first pipeline should not require pose estimation:

```text
camera view
   |
known ground reference / calibration
   |
manual take-off point
   |
manual landing point
   |
distance in metres
```

Pose/foot landmarks can later automate point proposals while preserving the same underlying
distance protocol.

### Homography

Known points on the ground can define a projective mapping between image coordinates and real-world ground coordinates.

This is particularly attractive because the relevant motion can be reduced to a plane rather than requiring full 3-D human reconstruction.

### Calibration candidates

For validation and early prototypes:

- ChArUco;
- ArUco;
- AprilTag;
- manually measured visible reference points.

A polished product may later allow a user to define virtual calibration points manually, but marker-based calibration is preferable for controlled validation.

### Event definition matters

"Broad-jump distance" requires a precise protocol definition.

For example:

- takeoff line;
- front of takeoff foot;
- rear-most landing contact;
- first stable landing;
- competition-style measurement rule.

The protocol must be persisted as part of the analysis provenance.

---

## 12. Sprint timing and split timing

Short sprint timing is a strong candidate for a single-phone virtual-gate approach.

The first implementation in #69 can avoid pose estimation and continuous tracking:

```text
0 m       5 m      10 m        20 m
 |---------|---------|-----------|
       known measured gates

manual / algorithmic crossing events
              |
       split timestamps
              |
     distance / split time
```

Once this event-based construct is validated against timing gates, a later continuous-speed path
can add a calibrated body trajectory and pose/landmark evidence.

### Reference point

Do not use a changing detector bounding-box edge as the timing point.

Potentially better references include:

- pelvis centre;
- torso centre;
- another stable pose-derived body landmark.

The chosen construct must be explicit and validated.

### Gate crossing

Do not manufacture sub-frame precision.

For the first event-only path, retain the bracketing frame timestamps (or an equivalent bounded
event interval) and expose the temporal uncertainty when the crossing lies between frames.

If a later calibrated body trajectory brackets a gate crossing, a sub-frame crossing time may be
estimated from the actual timestamps and an explicit interpolation model. That interpolation policy
must be versioned and validated against reference timing; linear interpolation is an assumption, not
ground truth.

This avoids both unnecessary frame quantization and plausible-looking fabricated precision.

### Start protocol

Different start definitions produce different results:

- first detectable movement;
- torso crossing a start plane;
- manual start event;
- audio stimulus;
- visual flash stimulus;
- external timing trigger.

OpenBar should not label all of these simply as "10 m time".

The timing protocol must be part of the result.

---

## 13. Frame rate and timestamp handling

High frame rate is especially important for jump takeoff/landing and sprint split timing.

Nominal temporal resolution:

| Frame rate | Frame interval |
| ---: | ---: |
| 30 fps | 33.33 ms |
| 60 fps | 16.67 ms |
| 120 fps | 8.33 ms |
| 240 fps | 4.17 ms |

### Recommendation

- barbell analysis: support ordinary frame rates where validation permits;
- for jump/sprint timing, benchmark 60/120/240 fps (and device-specific alternatives) inside the
  supported recording envelope rather than declaring one universal minimum in advance;
- use the highest validated capture mode whose delivered timestamps, exposure/motion blur,
  resolution, field of view, and sustained frame delivery remain acceptable;
- always use actual frame timestamps rather than deriving time from nominal FPS.

The existing OpenBar timestamp-over-nominal-FPS rule must remain authoritative.

---

## 14. Native camera APIs instead of Flutter-owned capture

Flutter should own UX, not the authoritative capture pipeline.

As of 2026-10-02, CameraX 1.6.x exposes stable high-speed session APIs, while Camera2 remains the lower-level Android escape hatch for constrained high-speed modes and device-specific control. OpenBar should query device-reported capabilities and benchmark actual frame delivery/timestamps; support in an OEM camera app is not sufficient evidence that the same mode is available through the application API.

Recommended future capture architecture:

```text
Flutter
  |
platform bridge
  |
  +-- iOS AVFoundation
  +-- Android Camera2 / CameraX
  |
hardware-backed buffers + timestamps
  |
vision / inference / Rust measurement
```

Reasons:

- high-speed camera modes are platform-specific;
- camera format selection is platform-specific;
- buffer handling and zero/low-copy paths matter;
- authoritative capture timestamps must be preserved;
- camera exposure and motion blur directly affect measurement quality.

Avoid repeatedly copying complete frames across Flutter FFI boundaries.

Return compact observations/results instead.

---

## 15. ONNX as model boundary

The current ONNX decision remains appropriate.

Recommended principle:

```text
PyTorch research / training
      |
canonical exported model
      |
     ONNX
      |
production inference adapter
```

But ONNX should be considered the **portable model contract**, not necessarily the only runtime representation.

A future deployment benchmark may produce:

```text
canonical model: ONNX

production target:
  - ONNX Runtime
  - Core ML
  - NNAPI
  - ncnn
  - another proven platform path
```

Selection should be based on:

- accuracy parity;
- latency;
- throughput;
- warm-up;
- memory;
- battery;
- package size;
- supported operators;
- device coverage.

---

## 16. Candidate pose technologies

Initial benchmark candidates should include at least:

### RTMPose / MMPose

Pros:

- mature research ecosystem;
- Apache-2.0 framework;
- designed for efficient pose inference;
- available deployment tooling.

Risks:

- Apache-2.0 describes the MMPose framework code; it does **not** automatically establish rights for every downloaded RTMPose checkpoint or its training datasets;
- each exact checkpoint/model asset needs a separate source, licence, training-data/provenance, redistribution, and commercial-use review before it can ship;
- production mobile latency must be benchmarked on real devices.

### Apple Vision

Useful as:

- iOS-native baseline;
- hardware/software integrated implementation;
- comparison point for latency and energy.

Risks:

- platform-specific;
- model behavior/version may be less controlled;
- reproducibility across OS versions needs consideration.

### MediaPipe Pose

Useful as:

- mobile performance baseline;
- readily usable pose system.

Risks:

- the MediaPipe framework code is Apache-2.0, but any bundled/downloaded model asset still needs an explicit provenance and redistribution/commercial-use review;
- current API/product terms and long-term dependency behavior should be assessed before foundational adoption.

### Recommendation

Benchmark, do not choose based on reputation alone.

---

## 17. Detection strategy

Detection is useful for:

- initial athlete localization;
- plate localization;
- crop selection;
- reacquisition after tracking loss.

It does not need to be the authoritative trajectory generator.

A good production pattern may be:

```text
periodic detector
       |
target initialization / correction
       |
higher-rate tracker
       |
raw trajectory
```

This can reduce compute while improving temporal stability.

The existing OpenBar future pipeline already anticipates detector-at-lower-rate plus tracker-at-higher-rate operation.

---

## 18. 100 m requires separate treatment

A single stationary phone is a plausible architecture for short sprint splits when the athlete occupies enough pixels and the ground plane is well calibrated.

A 100 m sprint creates additional problems:

- insufficient athlete resolution if the full course is visible;
- perspective distortion over large distance;
- panning invalidates simple static-camera geometry;
- autofocus/exposure changes;
- heat shimmer and environmental conditions;
- finish-event precision.

Therefore 100 m should not simply be an extension of the 20 m feature.

Possible future architectures:

### A. Multiple synchronized phones

```text
start phone ---- intermediate phone(s) ---- finish phone
        \________ synchronization ________/
```

Requires:

- clock synchronization;
- device-offset estimation;
- drift handling;
- trigger/event correlation;
- failure detection.

### B. Specialized finish/start capture

Use separate devices for start and finish with a shared synchronization mechanism.

### C. External timing integration

Later integration with photocells or timing gates could provide reference or hybrid timing.

### Recommendation

Do not promise accurate 100 m timing until a dedicated validation program proves an architecture.

---

## 19. Validation strategy by protocol

Each protocol should have its own ground-truth hierarchy.

### Barbell

Potential references:

1. controlled geometry / motion rig;
2. encoder / linear position transducer;
3. validated commercial VBT device;
4. high-frame-rate reference video;
5. manual digitisation.

### Vertical jump

Potential references:

1. force plate;
2. contact mat / validated timing system;
3. high-frame-rate reference video;
4. manual event annotation.

### Broad jump

Potential references:

1. tape/laser distance under explicit protocol;
2. fixed calibrated overhead/side reference;
3. manual annotated reference.

### Sprint

Potential references:

1. photocells;
2. fully automatic timing where appropriate;
3. high-speed reference video;
4. manual annotation for intermediate development fixtures.

### Metrics

Protocol benchmarks should report more than correlation.

Use as appropriate:

- MAE;
- RMSE;
- bias;
- standard deviation of differences;
- confidence intervals;
- Bland-Altman analysis;
- ICC where justified;
- missed-event rate;
- false-event rate;
- tracking availability;
- maximum lost interval;
- repeat-analysis determinism;
- failure rates by recording condition.

---

## 20. Confidence and unsupported conditions

Every protocol should be able to fail explicitly.

Examples:

### Lift

- plate occluded;
- target lost;
- insufficient calibration;
- camera moves;
- severe blur.

### Jump

- feet leave frame;
- landing occluded;
- pose confidence insufficient;
- takeoff event ambiguous.

### Broad jump

- takeoff line not visible;
- landing foot obscured;
- calibration plane invalid.

### Sprint

- athlete leaves calibrated region;
- multiple athletes cannot be disambiguated;
- camera moves;
- gate geometry becomes invalid;
- event crossing confidence insufficient.

Do not silently fill these failures with plausible-looking metrics.

---

## 21. Local-first storage and provenance

The broader product strengthens the need for a stable canonical analysis model.

Each result should eventually include:

- source media identity/hash;
- source dimensions/orientation;
- frame timestamps;
- capture metadata;
- camera/device identity where useful;
- calibration method and parameters;
- raw observations;
- derived physical trajectory;
- filter and parameters;
- protocol definition;
- event definitions;
- model name/checksum/version;
- tracker implementation/version;
- pipeline version;
- confidence/quality flags;
- unsupported-condition warnings.

Suggested storage direction:

```text
SQLite
  - sessions
  - athletes
  - attempts
  - metadata/indexes

media files
  - original recordings
  - optional derived renders

canonical JSON
  - portable analysis interchange
  - reproducible result record
```

CSV can remain a convenience export.

---

## 22. Illustrative post-re-entry sequencing (non-canonical)

This section is **not** the project roadmap and does not authorize M1 work.

ADR-0008 currently records **NO-GO / REWORK**. The project must first complete the re-entry evidence in #57, #53, #58, and #59 and record a new **GO** or **CONDITIONAL GO** review before automatic detection, Flutter/mobile measurement integration, or other M1 product-layer work becomes a production milestone.

### Current — M0 rework and re-entry

Finish the accepted re-entry path:

- select/reject the tracker and filter on held-out real-video evidence (#57);
- establish a supported recording envelope (#53);
- validate ROM/mean/peak velocity against an independent physical reference (#58);
- measure the frozen pipeline on the designated phone-class reference hardware (#59);
- perform the explicit re-review required by ADR-0008.

Only after that gate passes should the following stages be scheduled.

### Stage A — robust barbell target handling

Potential scope:

- detector-assisted initialization;
- reacquisition;
- stronger tracking;
- recording-envelope expansion;
- more device testing.

### Stage B — barbell product integration

Potential scope:

- native camera capture;
- thin Flutter UX;
- local session history;
- stable export;
- device performance validation.

### Stage C — vertical-jump research spike

Compare:

- flight-time estimation;
- takeoff-velocity estimation;
- pose candidates;
- force-plate/reference ground truth.

### Stage D — short-sprint research spike

Start with:

- 5/10/20 m;
- static camera;
- calibrated virtual gates;
- 120+ fps where supported and validated;
- photocell ground truth.

### Stage E — broad-jump research spike

Validate:

- ground-plane calibration;
- foot landmark robustness;
- takeoff/landing protocol;
- physical-distance reference.

### Stage F — unified athlete-testing architecture decision

Only after individual protocols have demonstrated acceptable measurement quality:

- compare real lift/jump/sprint evidence and result shapes;
- decide through an ADR whether a common protocol interface is justified;
- then consider athlete test sessions, longitudinal analytics, comparative dashboards, and shared calibration/camera UX.

### Later

- 30/40 m expanded sprint envelope;
- multi-device timing;
- 100 m;
- BLE/VBT sensor fusion;
- coach/team workflows;
- cloud sync;
- optional AI interpretation.

---

## 23. Competitive differentiation

Metric and WL Analysis are examples of products in the camera-based athlete-measurement category. Their existence shows category activity, but this document does **not** treat that as market-size or willingness-to-pay validation.

OpenBar should not primarily differentiate by claiming a better detector.

A stronger product position is:

```text
transparent measurement
+ explicit uncertainty
+ documented calibration
+ versioned algorithms
+ local-first processing
+ raw observation preservation
+ reprocessing
+ open export
+ published validation
```

Potentially differentiating capabilities:

- visible measurement confidence;
- traceable model/filter versions;
- ability to re-run old footage using a newer pipeline;
- published supported recording envelope;
- benchmark datasets and validation reports;
- cross-protocol athlete history;
- explicit distinction between measured and inferred quantities.

This also creates a better foundation for future AI explanations: AI can explain validated structured results rather than becoming the measurement source of truth.

---

## 24. Concrete recommendations

### Adopt / retain

1. **Retain Rust as the authoritative production measurement core.**
2. **Retain Python/PyTorch for ML and CV research.**
3. **Retain ONNX as the preferred portable model boundary.**
4. **Use OpenCV heavily in research and selectively behind an adapter in production.**
5. **Use native AVFoundation and Camera2/CameraX for future high-speed capture.**
6. **Retain Flutter as a thin application layer after the measurement engine is validated.**
7. **Benchmark RTMPose/MMPose as the initial learned-pose candidate.**
8. **Evaluate ChArUco/ArUco/AprilTag for controlled calibration workflows.**
9. **Model measurement protocols explicitly rather than encoding protocol assumptions in UI.**
10. **Persist raw observations, timestamps, confidence, provenance, and protocol definitions.**
11. **Use real ground truth: force plates, photocells, encoders, controlled geometry, or equivalent.**
12. **Treat accuracy validation as a product feature.**

### Avoid for now

1. Do not make Ultralytics a required production dependency without an explicit licensing decision.
2. Do not widen M0 to include pose, sprint, jump, Flutter, or live camera processing.
3. Do not assume one detector/tracker is appropriate for every protocol.
4. Do not make nominal FPS authoritative for timing.
5. Do not let Flutter own high-speed capture or measurement logic.
6. Do not treat acceleration as production-ready merely because position can be differentiated twice.
7. Do not present barbell-derived mechanics as whole-body force/power.
8. Do not promise accurate 100 m timing from a single phone without dedicated validation.
9. Do not adopt pretrained weights before auditing their redistribution/commercial/data provenance.
10. Do not choose filters, trackers, models, or inference runtimes without comparative benchmarks.

---

## 25. Recommended near-term engineering actions

These are recommendations for later work and should **not** interrupt current M0 unless explicitly prioritized.

### R1 — preserve generality in the core observation model

Review whether current trajectory and observation types accidentally assume a barbell.

Do not create generic abstractions prematurely, but avoid names or invariants that make future body/foot/athlete observations impossible.

### R2 — promote the protocol boundary to implementation ADR/API only after the second protocol exists

The architecture now records a candidate `MeasurementProtocol` boundary so today's design does
not accidentally block jump/sprint support without prematurely accepting that boundary.

Do not, however, invent a large generic Rust framework before M0 is complete.

A better trigger for committing the concrete trait/types/crates is:

> implement the first jump research spike, compare its real evidence/events/results with lift
> analysis, then extract the smallest shared abstraction.

### R3 — create a dependency/licensing ledger

For every future CV/ML dependency record:

- source;
- exact version;
- license;
- model-weight license;
- training-data provenance;
- redistribution implications;
- native/mobile implications.

### R4 — create device reference hardware tiers

Eventually define representative devices for:

- low-end Android;
- mid-range Android;
- flagship Android;
- supported iPhone baseline;
- recent iPhone.

Measure:

- capture modes;
- sustained FPS;
- inference latency;
- memory;
- battery/thermal behavior.

### R5 — build protocol-specific validation plans before product UI

Before implementing polished jump/sprint UX, create:

- ground-truth setup;
- fixture format;
- error metrics;
- recording envelope;
- go/no-go criteria.

### R6 — keep research models swappable

Production interfaces should consume generic observations/landmarks rather than MMPose- or YOLO-specific object types.

### R7 — treat event timing as its own validated subsystem

Takeoff, landing, rep phase changes, and gate crossings are measurements.

They should have:

- raw evidence;
- algorithm version;
- confidence;
- interpolation policy;
- validation metrics.

---

## 26. Decision summary

### Recommended

```text
Rust measurement core
Python/PyTorch research
ONNX model boundary
OpenCV as selective CV infrastructure
RTMPose/MMPose as a pose benchmark candidate
native camera/media APIs
Flutter as thin UI
validation-first development
```

### Not recommended as foundational architecture

```text
Ultralytics-centric application
YOLO-for-everything
Flutter-owned CV pipeline
Python-owned production kinematics
unvalidated acceleration
single-phone 100 m claims
opaque confidence-free measurements
```

### Most important conclusion

OpenBar's architecture should optimize for **trustworthy physical measurement**, not for the quickest demo.

The difficult and defensible part of this product is not recognizing a plate, person, foot, or pose.

It is establishing a reproducible chain from:

```text
pixels + timestamps
        ->
calibrated observations
        ->
validated physical quantities
        ->
explicit uncertainty
```

The current M0 architecture is already aligned with that goal. The recommended path is to finish proving the barbell measurement chain first, then add jump and sprint as independently validated protocols and only afterward extract the common athlete-measurement platform architecture.

---

## References and further evaluation sources

These links are starting points for future spikes. Exact dependency, model-weight, and dataset terms must be rechecked at adoption time.

- OpenBar architecture: `docs/architecture/ARCHITECTURE.md`
- OpenBar M0 roadmap: `docs/roadmap/M0.md`
- OpenBar validation documentation: `docs/validation/`
- OpenBar licensing strategy: `docs/legal/LICENSING_STRATEGY.md`
- OpenBar clean-room rules: `docs/clean-room/COMPETITOR_BOUNDARIES.md`
- Ultralytics repository: https://github.com/ultralytics/ultralytics
- Ultralytics licensing: https://www.ultralytics.com/license
- OpenCV repository: https://github.com/opencv/opencv
- OpenCV licensing: https://opencv.org/license/
- Android CameraX release notes: https://developer.android.com/jetpack/androidx/releases/camera
- Android Camera2 high-speed capability reference: https://developer.android.com/reference/android/hardware/camera2/CameraCharacteristics#REQUEST_AVAILABLE_CAPABILITIES_CONSTRAINED_HIGH_SPEED_VIDEO
- MediaPipe repository/licence: https://github.com/google-ai-edge/mediapipe
- MMPose repository/licence: https://github.com/open-mmlab/mmpose
- RTMPose/MMPose deployment documentation: https://github.com/open-mmlab/mmpose/tree/main/projects/rtmpose
- ONNX Runtime mobile documentation: https://onnxruntime.ai/docs/tutorials/mobile/
- AprilTag repository: https://github.com/AprilRobotics/apriltag
- Apple Vision human body pose documentation: https://developer.apple.com/documentation/vision/detecting-human-body-poses-in-images
- Android Camera2 documentation: https://developer.android.com/reference/android/hardware/camera2/package-summary
- Metric: https://metric.coach/
- WL Analysis: https://wlanalysis.com/

