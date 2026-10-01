# Supported recording envelope

Issue: #15  
Evidence source: [M0_EVIDENCE_REPORT.md](M0_EVIDENCE_REPORT.md) / #14

## Purpose

This document defines the M0 recording-support policy without turning sparse development evidence
into accuracy claims. The current evidence package proves deterministic execution and exercises a
small synthetic workflow fixture, but it does **not** contain held-out, annotated, non-synthetic
validation material sufficient to establish numeric recording limits.

OpenBar therefore distinguishes:

- **supported** — evidence demonstrates the condition is inside the supported envelope;
- **warning** — the pipeline may execute, but evidence is insufficient for an unqualified metric claim;
- **unsupported** — geometry or camera motion does not support the M0 physical interpretation;
- **unknown** — the relevant boundary has not been measured.

At the current M0 evidence state there is deliberately **no real-world condition classified as
fully supported**. Declared side-view + fixed-camera footage is warning-only.

## Evidence inventory

The #14 public package currently contains one synthetic development fixture:

- clean;
- 12 fps nominal/measured;
- 320x240;
- declared side view;
- fixed camera;
- good lighting;
- no authored motion blur;
- intermittent plate visibility / moderate occlusion including one fully occluded, unlabelable frame;
- 10 comparable labelled samples.

The local/private evidence report adds two 30 fps snatch clips in development role, but they are not
held-out accuracy evidence and are not both annotated. Tracker state/runtime observations from those
clips therefore do not establish an accuracy envelope.

## Evidence-backed limit table

| Dimension | Range/condition actually exercised | Sample/evidence size | Observed impact | M0 classification | Evidence |
| --- | --- | ---: | --- | --- | --- |
| View | side only in public synthetic development fixture | 1 fixture / 10 comparable labels | deterministic pipeline exercise; not held-out real accuracy evidence | **warning** for declared side; **unsupported** for oblique_45, front, rear; **unsupported** when unknown | #14 report |
| Yaw from true side | no controlled yaw sweep | 0 boundary samples | error growth / foreshortening not measured | **unknown**; no degree cutoff is claimed | #14 report |
| Pitch / camera height | not varied systematically | 0 boundary samples | perspective/parallax effect not measured | **unknown** | #14 report |
| Camera roll | not represented by current fixture metadata | 0 | effect not measured | **unknown** | #14 report |
| Distance / framing / cropping | no controlled sweep | 0 boundary samples | tracker/calibration degradation not quantified | **unknown** | #14 report |
| FPS | 12 fps synthetic; 30 fps local development clips | no held-out non-synthetic accuracy set | no evidence for a minimum or preferred range | **unknown** | #14 report |
| Resolution / orientation | 320x240 synthetic public fixture; no controlled resolution sweep | 1 synthetic fixture | no minimum plate-pixel or resolution threshold established | **unknown** | #14 report |
| Motion blur / shutter | public fixture authored as none; realistic blur untested | 0 realistic boundary samples | failure threshold not measured | **unknown** | #14 report |
| Lighting / contrast | public fixture good lighting; no controlled contrast sweep | 1 synthetic condition | no failure threshold measured | **unknown** | #14 report |
| Compression quality | no controlled compression sweep | 0 | effect not measured | **unknown** | #14 report |
| Plate size in pixels | seed diameter is observable, but no size sweep exists | 0 boundary samples | credible minimum pixel diameter not established | **unknown** | #14 report |
| Visibility / occlusion duration | one synthetic fixture includes partial/full occlusion | 10 comparable labels plus one unlabelable full-occlusion frame | explicit loss is preserved; duration tolerance is not established | **unknown** | #14 report |
| Camera stability | fixed camera exercised; handheld/panning not validated | 1 synthetic fixed condition | a moving camera changes the image reference frame and can turn apparent plate motion into camera motion | **warning** for fixed; **unsupported** for handheld/panning/moving/unknown in M0 | #14 report |

The table intentionally does not convert the 12 fps / 320x240 synthetic fixture into a minimum
requirement. Those are tested values, not validated boundaries.

## View vocabulary

Runtime metadata reuses the #3 fixture vocabulary:

- side
- oblique_45
- front
- rear
- unknown

Slight obliqueness remains represented by side plus approx_yaw_deg; no second incompatible fixture
enum is introduced. Any non-zero authored yaw is therefore visible as oblique-side metadata, but
#14 does not provide enough evidence to turn that metadata into a numeric pass/fail cutoff.

## Critical metric rule

OpenBar must not present image-plane X displacement as meaningful sagittal horizontal displacement
when camera geometry does not support that interpretation.

For M0:

- oblique_45, front, rear and unknown views are rejected by analyze;
- handheld, panning, moving-other and unknown camera stability are rejected by analyze;
- for declared side + fixed camera, calibrated displacement and velocity may be emitted only with a
  recording-support **warning** while the envelope remains unvalidated;
- the recording-support result marks horizontal displacement, vertical displacement, plate-diameter
  scale and velocity as warning or unsupported alongside the overall status.

Rejecting non-side/moving geometry avoids a false precision problem that a numeric X/Y value plus a
small footnote would not solve.

## Machine-readable contract

openbar_core::recording_support is authoritative for the M0 policy.

RecordingSupportAssessment (schema_version = 1) contains:

- normalized recording conditions using the #3 view/movement/condition vocabulary;
- overall support status;
- per-dimension status and stable reason code;
- the #14 evidence reference;
- metric-level support status for horizontal displacement, vertical displacement, plate scale and
  velocity.

The JSON wire contract is
[recording-support-v1.schema.json](../../validation/schema/recording-support-v1.schema.json).

openbar analyze accepts:

- fixture mode: camera/condition metadata comes from the fixture manifest;
- direct-video mode: --camera-view and --camera-movement are required so OpenBar never silently
  assumes side/fixed geometry;
- --approx-yaw-deg, --approx-pitch-deg, --camera-roll-deg, and --camera-distance-m may add
  direct-video metadata without creating a support cutoff;
- --recording-support-output <path> writes the machine-readable assessment.

When the assessment is unsupported, the sidecar is written first (when requested) and the command
returns the existing unsupported error category / exit code 4 without producing canonical analysis
JSON.

## Calibration interaction

Plate-diameter calibration remains a numerical image-to-metre transform, not a proof that camera
geometry is valid.

For runnable side/fixed footage, analyze records calibration quality as warning/unassessed and
carries specific geometry warnings when authored metadata indicates yaw, pitch/height or roll.
Unsupported view/stability is rejected before physical analysis is emitted.

Perspective and plate foreshortening can bias apparent plate diameter, so a numerically valid
metres-per-pixel value must not be interpreted as validated physical scale outside the recording
envelope.

## Recording guidance

Until evidence expands, users should maximize the chance of obtaining interpretable M0 footage:

1. Use a fixed tripod/phone mount; do not pan or zoom during the lift.
2. Record from the side, with the camera axis as close to perpendicular to the sagittal plane as
   practical.
3. Keep the full plate trajectory in frame with margin around the plate.
4. Avoid strong camera pitch, very low/high camera placement, or visible roll where practical.
5. Use good, even lighting and clear plate/background contrast.
6. Prefer short exposure / low motion blur and avoid aggressive recompression.
7. Keep the plate unobstructed as much as possible.
8. Use enough resolution that the plate rim can be seeded clearly.

These are acquisition recommendations, **not** validated numeric thresholds.

## Answers to the #15 documentation questions

**How close to side-on?** No evidence-backed degree limit exists. Declared side/fixed footage is
warning-only; oblique_45, front, rear and unknown are unsupported. Authored yaw remains visible and
unknown rather than being compared with an invented cutoff.

**What FPS/resolution has actually been validated?** The public development fixture is 12 fps at
320x240. Local development clips are 30 fps. Neither establishes a real-world supported range.

**Minimum plate visibility/size?** Unknown. No credible minimum plate diameter in pixels has been
measured.

**Short versus long occlusion?** Unknown. The synthetic workflow proves explicit lost/unlabelable
handling, not an occlusion-duration tolerance.

**Camera movement tolerance?** Unknown. M0 requires fixed camera metadata. Handheld/panning/moving
footage is rejected because no motion-compensation model exists and the physical reference frame is
not stable.

**Which metrics become invalid first?** Horizontal displacement and isotropic plate-diameter scale
are directly vulnerable to yaw/foreshortening; camera motion contaminates both axes. Because the M0
pipeline derives velocity from those positions, unsupported geometry invalidates the resulting
physical kinematics as well.

## Evidence needed to promote warning/unknown states

The next envelope study should hold the tracker/filter configuration fixed and add held-out,
annotated, non-synthetic fixtures that vary one recording dimension at a time where practical:

- side-view yaw sweep including small obliqueness and approximately 45 degrees;
- camera height/pitch and distance/framing sweeps;
- multiple realistic FPS/resolution combinations;
- controlled plate-pixel-size and contrast changes;
- realistic blur/compression levels;
- short/medium/long occlusion spans;
- quantified minor handheld motion versus meaningful camera movement.

Each boundary decision should retain tested values, sample counts, error distributions/failure
impact and the resulting supported/warning/unsupported classification. Until then, unknown remains
the correct state.
