# ADR-0013: Timestamp-aligned video playback and bar-path overlay contract

- Status: Proposed
- Date: 2026-10-13
- Issue: #181 (child design of #80)
- Related: ADR-0001, ADR-0003, ADR-0006 (Proposed, headless M0 decode), ADR-0007, ADR-0008,
  ADR-0012 (Proposed), #179, #180
- Detail: [`docs/validation/VIDEO_OVERLAY_CONTRACT.md`](../validation/VIDEO_OVERLAY_CONTRACT.md)

## Context

The planned Android app shows the measured bar path directly over the imported lift video, with
results within seconds (#80). The M0 diagnostic renderer already draws canonical analysis over a
single decoded frame and keeps loss explicit, but it is a headless validation report. It has no
playback, no seeking, no player, and it does not have to agree with a player about orientation or time.

A player is a second implementation of the display path. The failure modes are quiet and look
plausible:

- the player and the analysis can disagree about rotation, pixel aspect ratio, container crop or
  decoder padding, which shifts or scales the path by a few pixels, or flips it;
- a player may rebase presentation time to zero or keep the container's value, so a clip with a
  non-zero `start_pts` shows the marker on the wrong frame;
- variable-frame-rate phone footage makes `frame = time × FPS` wrong by up to a fifth of a second
  by the end of a clip (ADR-0006 recorded 0.19 s on a real clip);
- a seek may land on an earlier keyframe;
- smoothing the marker between frames, or joining points across a lost span, draws positions the
  tracker never measured.

M1 is locked (ADR-0008, NO-GO / REWORK). The rep metrics contract it interacts with (ADR-0012) is
Proposed and its owner decisions are pending. This ADR is a design decision and authorises no
implementation, no player library, no decoder and no app scaffold.

## Decision

### 1. The overlay renders canonical raw display-pixel observations and computes nothing

The overlay consumes a validated `Analysis`: the display raster, `raw_observations` (timestamp, frame
index, tracking state, measured pixel centre) and `video.trim`. It does not read metric coordinates,
does not track, and does not edit an `Analysis` in place. A filtered or velocity-coloured path, if
ever shown, is a separate labelled derived layer and never replaces the raw path. Measurement logic
stays in `openbar-core` (ADR-0001).

### 2. One display transform, from the analysis raster

Canonical coordinates are in the analysis display raster (rotation applied, ADR-0006), with integer
coordinates at pixel centres (ADR-0007). The screen position is
`u = c_left + k·(x + 0.5)`, `v = c_top + k·(y + 0.5)`, with `k` taken from the **analysis** raster and
the content rect read from the layout that draws the video. The overlay never rotates a canonical
coordinate; rotation is verified, not applied. A player that decodes at reduced size is still
correct.

The overlay is drawn only after the file hash, pixel aspect ratio (1:1 or unknown), presented raster
(1 px tolerance) and applied rotation have been verified against the Analysis. A check that cannot be
performed (an Analysis without a source hash, a player that cannot report its rotation) is a refusal,
not a pass. Any failure plays the video without an overlay and names the reason. No best-effort
overlay is drawn. An aspect-preserving crop cannot be seen from dimensions and rests on the
conformance fixture.

### 3. Time is bound by presentation timestamps, never by FPS

At import the player adapter lists every frame's presentation timestamp. The overlay verifies that
the offset between those and the Analysis timestamps is the same for every observation (spread of at
most 1 µs) and then ignores whether the player rebases. The displayed frame is the one the player
reports as presented, or, from a clock only, an approximate estimate: the last frame at or before the
position. Frame-exact seek and stepping are reported only from presented-frame reports. The observation
is found by frame index. Metadata FPS is display text. `lost` and "not analysed" are different states.

### 4. Frame-locked marker, no measurement-looking animation

The marker changes only when a frame is presented and is hidden while a seek is pending. The first
version has no interpolation. Any later animation is presentation only, never across a lost
observation, never used for a readout, boundary, hit test, export or metric.

### 5. Loss and confidence stay explicit

A `lost` observation has no coordinate and breaks the path; so does a gap in frame index. A one-point
run is a dot. `low_confidence` is distinguishable without colour. The numeric confidence is shown only
as a tracker score. The overlay never connects across a loss or promotes a state to certainty.

### 6. Rep navigation follows ADR-0012 and is labelled an assumption

The overlay needs only each rep's seek target, span, availability and completeness. It seeks
frame-exact to the metric interval's first sample and highlights the raw path to the last sample.
Boundaries must equal observation timestamps or the highlight is withheld. A withheld rep shows its
window and break and no number. Rep results are valid only for the Analysis digest, rule version and
parameters they carry. Nothing in this ADR accepts ADR-0012.

### 7. Edits create new Analyses

Changing the plate target reruns tracking. Changing only the diameter reruns calibration, filtering,
kinematics and reps in `openbar-core` on byte-identical raw observations, and raw observations are
reused only if the tracker provenance shows the diameter is not a tracker input; otherwise tracking
reruns too. The old result is marked superseded and its metrics
are hidden. A failed or refused recompute keeps the previous result, labelled with the edit it does not
reflect. A cosmetic scale on displayed numbers is not a diameter edit.

### 8. Annotated video export is deferred

If it is built it is an explicit, separate, local job outside the playback and results critical path;
it re-encodes with per-frame timestamps preserved; its output is marked derived and is never an
analysis source. Its duration is not part of time-to-first-results.

### 9. No canonical contract changes

`analysis-v1`, its schema version constants, its golden file and every committed fixture are
unchanged. The diagnostic renderer is unchanged and remains a canonical consumer. The #79 study is
untouched.

## Alternatives considered

- **Let the player draw its own overlay from a path it tracks.** Rejected. A second tracker output that
  can disagree with the Analysis, and measurement logic outside `openbar-core`.
- **Map metric coordinates (metres) back to the screen.** Rejected. Calibration error would move the
  path off the plate, and the inverse projection only exists for a scalar scale.
- **Find frames by `round(time × FPS)`.** Rejected. Wrong on variable-rate footage and on clips with
  a non-zero start, and the contract already forbids it for measurement.
- **Use the nearest frame to a seek or clock position.** Rejected. It can show a marker for a frame
  that is not on screen. Floor of the presented frame is deterministic and testable.
- **Trust that the player and decoder agree about rotation and crop.** Rejected. Dimensions cannot
  detect 180° or a square raster, and a padded decoder raster is a common 8-pixel offset. Verification
  plus a rotated-fixture conformance test is the cheaper, evidence-bearing option.
- **Interpolate the marker for smooth playback.** Rejected for the first version. The video advances
  in discrete frames, and an interpolated marker drifts from the plate it marks and could cross a loss.
- **Persist overlay or navigation state inside `analysis-v1`.** Rejected. It would change a frozen
  contract and golden, and display state is not measurement.
- **Ship annotated video export with the first results screen.** Rejected. It adds re-encoding and
  an encoder decision to the first-results path and is outside what the owner asked to see first.

## Consequences

Positive:

- one transform and one timeline rule, testable without a device through dyadic reference cases;
- loss, low confidence and unverified alignment are visible instead of drawn as plausible paths;
- nothing frozen moves: `analysis-v1`, the diagnostic renderer and #79 are untouched;
- ADR-0012's pending decisions do not touch overlay geometry or time. They can change labels, and a
  changed rule version or interval changes rep seek targets and makes stored rep results stale.

Costs and risks:

- a player adapter that cannot list frame timestamps, report applied rotation or seek frame-exactly
  needs extra work (step-forward seek, a wrapper) or cannot be used;
- the 1 px raster and 1 µs timeline tolerances are proposals without evidence behind them;
- the checks cannot be proven on real devices from this repository: nothing here exercises a mobile
  player, container crop, a real non-zero-start or irregular-PTS file through the full decode, or a
  portrait-native recording;
- an unverifiable file shows no overlay, which is correct but visible to users;
- the overlay is only as correct as the Analysis. Silent false tracking (ADR-0008) draws a confident
  path over the wrong object; nothing in presentation can fix that.

## Owner decisions requested

1. **Refuse versus degrade.** Recommended: refuse the overlay on any failed check and play the video.
   Alternative: draw with a warning. The recommendation keeps a misaligned path from being read.
2. **Selected rep seek target.** Recommended: the metric interval's first sample (normally the
   turnaround), where the displayed duration and mean begin. Alternative: the window's first frame.
3. **First-release layers.** Recommended: raw path only. Alternative: add a labelled filtered path.
4. **Annotated video export.** Recommended: defer until after the first results release. Alternative:
   include a still image or vector path export first.
5. **Tolerances.** Whether 1 px (raster) and 1 µs (timeline) are acceptable starting values.

## Revisit when

- M1 entry is recorded GO or CONDITIONAL GO and a player, binding and UI toolkit are chosen;
- #179 sets numeric results-latency targets or measures an app path;
- ADR-0012 is accepted, amended or rejected;
- a real device shows a player disagreeing with these checks;
- non-square pixels, container crop, or a recording type outside side/fixed is admitted.
