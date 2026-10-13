# Video playback and bar-path overlay (design contract)

Issue: #181 (child design of #80). Decision record: [ADR-0013](../adr/0013-video-playback-and-bar-path-overlay-contract.md).

## Status

**Design only.** Nothing here is implemented in `openbar-core`, the CLI or any mobile app. M1 is locked
by [ADR-0008](../adr/0008-m0-go-no-go-and-m1-entry.md); this document does not unlock it. It changes no
`analysis-v1` field, schema version, golden file or measurement output, does not change the
diagnostic renderer, and does not touch the frozen #79 agreement study. No player library, decoder
or UI toolkit is chosen here (ADR-0006 keeps FFmpeg as the headless M0 decode boundary only).

It is the contract a future Android results screen is reviewed against. Everything it calls
"reference" is a synthetic engineering expectation, not accuracy evidence. Where it relies on the
**proposed** rep metrics contract ([ADR-0012](../adr/0012-portable-rep-and-phase-metrics-contract.md),
[`REP_PHASE_METRICS.md`](REP_PHASE_METRICS.md)), the dependency is labelled an assumption (Section 6).
ADR-0012's owner decisions are pending and are not treated as accepted.

## Governing constraints

- The overlay draws **canonical raw display-pixel observations** (`Analysis.raw_observations`). Not
  metric coordinates, not filtered values, not a path tracked again by the UI. Raw is never replaced
  (ADR-0003).
- A `lost` observation has no coordinate. The path breaks there; nothing is interpolated.
- Decoded presentation timestamps are authoritative. Metadata FPS may be displayed; it is never
  used to find a frame.
- Pixel coordinates follow ADR-0007: `(i, j)` is the centre of pixel `i`, `j`, and a consumer that
  draws pixels places a coordinate `x` at `x + 0.5` in edge units.
- Measurement logic stays in `openbar-core`. The results screen renders, seeks and navigates. It
  recomputes nothing and never edits an `Analysis` in place.
- Data stays local; no part of playback, overlay or results needs a network.
- Failing closed beats a plausible overlay. When a transform or timeline cannot be verified the video
  plays without an overlay and says why (Section 8).

## 1. What the overlay consumes

| Input | Source | Use |
| --- | --- | --- |
| Display raster `W_a × H_a` | `video.display_width_px`, `display_height_px` | the coordinate space of every canonical point |
| Coded raster, rotation | `video.decoded_width_px`, `decoded_height_px`, `source_rotation_deg` | orientation verification only |
| Source identity | `identity.source_sha256` | the Analysis belongs to this file |
| Observations | `raw_observations[i]`: `timestamp_s`, `frame_index`, `tracking_state` (`tracked`, `low_confidence`, `lost`), `measurement.x_px`, `y_px`, `confidence` | path, current marker, state |
| Analysed range | `video.trim` | frames outside it have no observation |
| Decoder provenance (optional) | `provenance.environment.decoder`, a JSON string written by the CLI: `stream.time_base`, `stream.start_pts`, `probed_frame_count` | advisory cross-check only; free-form, never required |
| Rep results (assumption A1) | a derived view keyed by Analysis digest and rule version | rep list and seek targets only (Section 6) |

Not consumed: calibrated metres, filtered or kinematic layers (the diagnostic report re-projects
filtered metres to pixels; the product overlay does not), `target_bounds_px` except as an optional
diagnostic layer, any tracker confidence as a probability. `measured_fps` and `nominal_fps` are
display text.

Gap found in the current Analysis: it carries no pixel aspect ratio and no crop.
ADR-0006 (Proposed; implemented in `apps/openbar-cli/src/media/`) rejects any sample aspect ratio
other than 1:1 (or FFmpeg's unknown 0:1) before analysis, so an Analysis implies square pixels. Crop
is not recorded anywhere and is not tested; the analysis raster is whatever FFmpeg decoded. The
overlay therefore verifies what it can on the playing file (Section 2) and states what it cannot.

`identity.source_sha256` and the decoder provenance are optional in the Analysis. The contract treats
a missing hash as a refusal (Section 2), not as a pass.

The analysed range is taken from the first and last observation, not from `video.trim`: tracking
starts at the seed frame, and `trim` can be wider than the frames that have observations.

"Analysis digest" below means a hash of the canonical serialized Analysis; its exact definition belongs
to ADR-0012's sidecar follow-up and is not defined here.

## 2. Coordinate spaces and display transform

| Space | Definition |
| --- | --- |
| S0 coded | the stored raster, `decoded_width_px × decoded_height_px`, after codec cropping |
| S1 display | S0 after the rotation in `source_rotation_deg`, `W_a × H_a`; **every canonical coordinate lives here** |
| S2 player | the raster the player presents, `W_p × H_p`; may be decoded at reduced size |
| S3 screen | device pixels, edge units: pixel `j` spans `[j, j+1)` |

The overlay starts in S1. It never rotates a canonical coordinate: rotation is the player's job and is
only *verified*.

### Rotation (verification and fixtures only)

`source_rotation_deg` is FFmpeg's display-matrix convention, counter-clockwise degrees applied to the
coded frame before display (ADR-0006). A coded pixel `(cx, cy)` in a `W_c × H_c` raster lands at:

| Rotation | S1 point | S1 size |
| ---: | --- | --- |
| 0 | `(cx, cy)` | `W_c × H_c` |
| 90 | `(cy, W_c − 1 − cx)` | `H_c × W_c` |
| 180 | `(W_c − 1 − cx, H_c − 1 − cy)` | `W_c × H_c` |
| 270 | `(H_c − 1 − cy, cx)` | `H_c × W_c` |

The same formulas hold for continuous coordinates under the pixel-centre convention.

### S1 → S3

Let the **content rect** `(c_left, c_top, c_w, c_h)` be the rectangle in S3 in which the video raster
is actually drawn. It is read from the layout that draws the video, so the video and the overlay
share one source of truth. For the default `contain` fit in a view of `V_w × V_h`:

```text
k      = min(V_w / W_a, V_h / H_a)
c_w    = k · W_a            c_h = k · H_a
c_left = (V_w − c_w) / 2    c_top = (V_h − c_h) / 2

u = c_left + k · (x + 0.5)
v = c_top  + k · (y + 0.5)
```

If the layout supplies the content rect, use `k_x = c_w / W_a` and `k_y = c_h / H_a`; the formulas
above are how the default rect is derived, and the two agree whenever the raster check below passes
and the layout preserves aspect ratio.

- The scale uses the **analysis** raster, not `W_p`. A player that decodes a 1080×1920 clip at
  540×960 therefore still places the overlay correctly.
- The `+ 0.5` is ADR-0007. Omitting it shifts the overlay half an analysis pixel up and left,
  which is the defect `diagnostic-svg@1` had. A toolkit that indexes pixels by centre subtracts 0.5
  from `u` and `v`.
- The inverse for hit testing (choosing a plate, editing a centre) is
  `x = (u − c_left) / k − 0.5` (under zoom and pan, first undo them: `u = V_w/2 + (u' − V_w/2 − p_x) / z`).
  A tap inside the content rect yields a continuous coordinate; a producer clamps it to
  `[0, W_a − 0.5) × [0, H_a − 0.5)`, the intersection of the ADR-0007 windows. A tap in a letterbox
  bar is rejected, not clamped.
- Letterbox and pillarbox bars lie outside the content rect. Nothing is drawn there; the path and
  marker are clipped to the content rect.
- `contain` is the only fit offered. A `cover` or fill fit crops the frame and can hide the path. A
  user zoom `z` and pan `(p_x, p_y)` in view pixels compose as
  `u' = V_w/2 + z · (u − V_w/2) + p_x` (likewise `v'`). Path width and marker size are in screen
  pixels, so they do not scale with `k` or `z`.

### Verification before any overlay is drawn

All of these must hold, otherwise the overlay is refused with the named reason and the video plays
unannotated.

| Check | Rule | Refusal |
| --- | --- | --- |
| Source | SHA-256 of the file being played equals `identity.source_sha256` (the app stores the import hash). An Analysis without a hash cannot be tied to a file | `source_mismatch`; `source_unverifiable` when the hash is absent |
| Pixel aspect | the file's sample aspect ratio is 1:1 or unknown (0:1) | `unsupported_sample_aspect_ratio` |
| Raster | `|W_p − W_a·H_p/H_a| ≤ 1` and `|H_p − H_a·W_p/W_a| ≤ 1` (proposed tolerance, one player pixel). A transposed raster, a padded decoder raster (1920×1088 for 1920×1080) or a crop that changes the aspect ratio fails. A crop that preserves the aspect ratio cannot be detected from dimensions | `raster_mismatch` |
| Orientation | the net rotation the player reports having applied equals `source_rotation_deg` (mod 360). Dimensions alone cannot tell 0° from 180°, or any rotation of a square raster. A player that cannot report it needs a recorded conformance pass for the device class | `orientation_mismatch`; `orientation_unverifiable` when neither is available |
| Conformance | the adapter has passed the rotated synthetic fixture on the device class: 90/180/270 copies of an asymmetric image with markers at all four frame corners, every marker pixel checked, as ADR-0006 does for decode. This is also the only check for an aspect-preserving crop | build-time gate, not a runtime refusal |

Non-square pixels are reserved, not enabled: if a later ADR admits them, the horizontal factor
becomes `k · SAR` and calibration must be revisited, because a plate is no longer circular in S1.
An app-level video crop or re-encode that changes pixels is a different file with a different hash
and needs its own Analysis; the zoom above is a view transform and is not a crop. Container crop
(a clean aperture or conformance window) is an unverified assumption until a real file with one passes
through the decode and the conformance fixture.

## 3. Timeline and seek

The authoritative timeline is the decoder's presentation timestamp (PTS): `timestamp_s =
(pts − start_pts) · time_base`, strictly increasing, computed in integer ticks (ADR-0006). The player
timeline may differ by an offset: some stacks rebase to zero at the first frame, others keep the
container's PTS, and a clip with a non-zero `start_pts` shows the difference.

### Timeline binding (once per import, before any marker)

The adapter must expose, without decoding, the ordered presentation timestamp `P_f` (integer µs) of
every frame `f` of the video stream, where `f` is the frame's rank in presentation order (the
`frame_index` of ADR-0006; B-frame reordering is not yet exercised on real footage). How the adapter
reads them is an implementation choice. For every observation `i` with canonical timestamp `t_i` and
`frame_index` `f_i`:

```text
C_i = round_half_up(t_i · 1e6)          integer µs
d_i = P_{f_i} − C_i
```

The binding holds when `max(d_i) − min(d_i) ≤ 1` µs (rounding of `t_i`; proposed tolerance), and the
offset is `d = min(d_i)`. `d` is used only to show canonical time for a frame, including frames
outside the analysed range. Frame lookup and seek targets use `P_{f_i}` through the frame index and
never depend on `d`. Otherwise the overlay is refused (`timeline_mismatch`). It is also refused
(`timeline_unavailable`) when an observation has no `frame_index` or the adapter cannot list frame
timestamps. When the optional decoder provenance parses, `probed_frame_count` must equal the player's
frame count; a difference is `timeline_mismatch`. So is an observation whose `frame_index` has no
player frame.

Binding means the overlay does not need to know whether the player rebases. It also catches the wrong
file, a re-muxed copy, edit lists that move frames, and a frame-count disagreement before a wrong
marker is shown.

### Which frame is on screen

1. **Frame-presented timestamp (preferred).** Each time the player presents a frame it reports that
   frame's `P_f`. The displayed frame is exactly `f`.
2. **Playback clock (fallback, approximate).** With only a clock position `pos`, the displayed frame
   is estimated as the last `f` with `P_f ≤ pos` (floor). Never the nearest frame, never
   `round(pos · fps)`. Render latency and vsync make this an estimate; it is acceptable for the moving
   marker during playback but not for reporting a frame-exact seek or a step as successful, which
   needs rule 1. `pos < P_0` shows no frame yet. After the last frame the last frame stays.

Then the observation is looked up by frame index, not by time:

| Result | Condition | Marker |
| --- | --- | --- |
| `tracked` or `low_confidence` | observation exists with a measurement | drawn at its coordinate |
| `lost` | observation exists, state `lost` | none; readout says tracking lost |
| `not_analysed` | no observation for `f`: outside `video.trim`, before the seed frame, or not emitted | none; readout says not analysed |
| `none` | clock position before the first frame (`pos < P_0`): no frame is displayed yet | none; no readout |

`lost` and `not_analysed` are different states and are worded differently.

### Seek

- A seek is **requested** at a position and **completes** when a frame is presented. The marker and
  readout follow the frame actually presented, never the requested position, because a stack may land
  on an earlier keyframe.
- While a seek is pending the marker and readout are hidden (`seeking`). The path stays, since it is
  static data. A stale marker is not shown.
- Navigation to a sample (a rep, a loss span, frame stepping) needs **frame-exact** seek. The
  adapter may seek earlier and step forward, but it reports success only when the presented `P_f`
  equals the target. It never reports success on a different frame (`seek_not_exact` is surfaced).
- Frame stepping moves by frame index, including through `lost` and `not_analysed` frames. Irregular
  timestamps change how long a step takes, not which frame is next. Stepping past the first or last
  frame of the video does nothing.
- FPS is shown as text (`video.frame_rate.measured_fps`, and `nominal_fps` where recorded). Variable
  frame rate, rotated, portrait, non-zero-start and irregular-timestamp clips are all handled by the
  two rules above and are pinned by the reference cases.

### No display interpolation

The marker is frame-locked: it changes only when a frame is presented. The first version does not
interpolate between observations for smoothness, because the video itself advances in discrete frames
and an interpolated marker would drift from the plate it marks. If a later version animates anything,
it is presentation only: between two adjacent `tracked`/`low_confidence` observations, never across a
`lost` observation or outside the analysed range, never used for a readout, a rep boundary, a hit
test, an export or any metric, and identified as such in code. Trail fade and similar effects are
presentation too.

## 4. Path, marker, gaps and confidence

Layers, bottom to top: video, measured path, rep highlight, current marker, readout and scrubber.

- **Path segments.** Walk `raw_observations` in order. A segment joins two consecutive observations
  only if both have a measurement (`tracked` or `low_confidence`) **and** their `frame_index` values
  are consecutive. A `lost` observation, or a gap in `frame_index`, ends the current run; the next
  measured observation starts a new one. A run of one observation is drawn as a dot. A run never
  begins or ends at a lost observation: nothing is drawn there, since it has no coordinate.
- **Trail.** The portion up to the displayed frame is emphasised and the remainder dimmed. Both come
  from the same segments; the emphasised part is the same segments cut at the displayed frame. A cut
  at a run's first observation emphasises only that observation, drawn as a dot. The displayed frame
  of a `lost` observation cuts the run before it; nothing is emphasised at the loss.
- **Low confidence.** Distinguishable without colour: the marker is a ring where a tracked marker is
  filled, and a segment with a `low_confidence` endpoint is dashed where others are solid. The legend
  names both. The numeric value, if shown, is labelled a tracker score, not a probability or an
  accuracy; ADR-0008 records that a tracker can stay high-confidence on the wrong target.
- **Loss.** The scrubber shades `lost` spans and marks `low_confidence` frames, taken from the
  observations, so a user can find failures without scrubbing the whole clip.
- **Visibility.** `visibility` other than `visible` is readout text only in v1.
- **Optional layer.** `target_bounds_px` may be drawn from the raw bounds as a diagnostic toggle,
  off by default.
- **Filtered or velocity-coloured paths** are derived layers. If ever added they are a separate,
  labelled toggle that never replaces the raw path (ADR-0003) and break at `lost` observations the
  same way. Not in the first version.

## 5. Initial results controls

| Control | Behaviour | Source |
| --- | --- | --- |
| Play, pause, speed | video playback; marker follows presented frames | player |
| Scrubber | position over the video; shows analysed range, loss spans, low-confidence frames, rep spans | observations, A1 |
| Step ±1 frame | frame-exact seek by frame index | Section 3 |
| Rep list, previous, next | Section 6 | A1 |
| Path toggle | off, full, or trail emphasis (default on) | Section 4 |
| Zoom and pan | view transform only | Section 2 |
| Readout | frame index, PTS and canonical timestamp, state, `x_px`, `y_px`, tracker score (as a score) | observation |
| Status chip | recording-support state and calibration warnings, as the Analysis records them. The overlay never upgrades a warning | Analysis |
| Target and diameter edit | Section 7 | seed, calibration |

Diameter is shown as an editable assumption starting at 0.45 m (#80 product brief; behaviour as in
[`AUTOMATIC_PLATE_SELECTION_PLAN.md`](../plans/AUTOMATIC_PLATE_SELECTION_PLAN.md)). Not in the first
version: drawing tools, editing individual points, a filtered path, annotated video export.

## 6. Rep navigation (assumption A1: ADR-0012 proposed, not accepted)

The overlay needs only these fields per rep from the proposed rule `concentric-reps@1`:
`rep_index`, `interval_start_timestamp_s`, `end_timestamp_s`, `availability`, `withheld_reasons`,
`completeness`. The overlay computes none of these. A change to ADR-0012's rule version, interval or
metric definitions invalidates stored rep results (`rep_result_stale`), and seek targets then follow
the new fields; a change that only affects labels or units changes displayed text.

- **List.** Every rep is listed in order, including `withheld` ones with their reasons and no numbers
  (never zero), and `incomplete`/`undetermined` ones flagged. `rejected_runs` are a diagnostic and are
  not in the primary list.
- **Seek.** Selecting a rep seeks, frame-exact, to `interval_start_timestamp_s`: the metric interval's
  first sample: normally the turnaround sample, or the last trimmed frame when the rule trimmed
sub-floor leading frames (`leading-creep-trimmed-window`). That is where the displayed duration, ROM
and mean begin.
  Selecting a rep does not play it.
- **Highlight.** The raw path between `interval_start_timestamp_s` and `end_timestamp_s` inclusive is
  highlighted. A `lost` observation inside it still breaks the highlight; a withheld rep shows its
  window and break and no number. The scrubber shows the span.
- **Boundaries are observations.** Both timestamps must equal an observation timestamp exactly
  (the `MetricInterval` rule). Otherwise the highlight is not drawn and the rep is flagged
  `rep_boundary_not_an_observation`; nothing is snapped to the nearest frame. The seek target is
  withheld only when the *start* boundary is not an observation, since the end boundary plays no part
  in a seek (`boundary-not-an-observation` pins both outcomes).
- **Numbers.** Shown only for a `reported` rep, labelled "upward" with the rule id and version and the
  unvalidated marker until ADR-0012's label decision is recorded. The overlay formats them; it does not
  derive them from pixels.
- **Validity.** A rep result is valid for the Analysis digest, rule id and version, and parameters it
  carries. On any mismatch the rep list is hidden as `rep_result_stale` and the path still plays. A
  declared lift without a supported profile shows path and playback and "not supported for this lift".

## 7. Editing target or diameter

An edit produces a **new Analysis with its own provenance**; it never relabels the old one.

| Edit | What changes | What is recomputed |
| --- | --- | --- |
| Plate target (choose another plate, move the centre, change the radius or seed frame) | the seed, so the tracker input | tracking, calibration, filter, kinematics, reps |
| Plate diameter only | calibration scale | calibration, filter, kinematics, reps, by `openbar-core` on the same raw observations. The raw pixel path is byte-identical; a test asserts it. Reuse is allowed only if the tracker provenance shows the diameter is not a tracker input; otherwise tracking reruns |

- On the edit the previous result is marked `superseded`. Its metrics are hidden, not greyed, so a
  possibly wrong number is never read as current. Its path may stay dimmed and labelled "previous
  result". New results replace it atomically after the new Analysis validates and Section 2 and 3 are
  re-verified.
- If the recompute is refused or fails, the previous result stays available labelled with the edit it
  does not reflect. The edit is never silently converted into success.
- A cosmetic scale on displayed numbers is not an acceptable implementation of a diameter edit.
- The original proposal and each correction are retained separately, as the #178 plan requires. That
  record is not an `Analysis` field and not defined here.
- The seed preview uses the same transform and its inverse (Section 2).

## 8. Refusal reasons

| Reason | Meaning |
| --- | --- |
| `analysis_invalid` | the Analysis fails `openbar-core` validation |
| `source_mismatch` | file hash differs from `identity.source_sha256` |
| `source_unverifiable` | the Analysis has no `identity.source_sha256` |
| `unsupported_sample_aspect_ratio` | pixel aspect ratio is not 1:1 or unknown |
| `raster_mismatch` | the player raster does not match the analysis raster |
| `orientation_mismatch` | the player's applied rotation differs from `source_rotation_deg` |
| `orientation_unverifiable` | the player cannot report its applied rotation and no conformance pass is recorded |
| `timeline_unavailable` | no frame timestamps from the player, or an observation lacks `frame_index` |
| `timeline_mismatch` | binding offsets spread by more than 1 µs, or frame counts differ |
| `seek_not_exact` | the presented frame is not the requested sample |
| `rep_boundary_not_an_observation` | a rep boundary is not an observation timestamp |
| `rep_result_stale` | rep results do not match the Analysis they are shown with |
| `analysis_superseded` | an edit replaced this Analysis |

The reasons up to and including `timeline_mismatch` withhold the overlay; the video still plays. No partial overlay is drawn against an
unverified transform or timeline.

## 9. Export

**Annotated video export is deferred.** It is not part of the first results experience and needs the
M1 decision and a separate encoder decision. If it is added:

- it is an explicit user action, a separate background job, and never on the critical path of
  playback or results. Its duration is excluded from time-to-first-results (#179 owns the numeric
  targets);
- it re-encodes, so it records the encoder and settings, preserves per-frame timestamps (no
  constant-frame-rate conversion of a variable-rate clip), writes upright pixels and states its
  orientation, and draws with this transform and Section 4's rules, with no marker on `lost` frames;
- it runs entirely on the device and needs no network permission;
- the output is marked derived and is never accepted as an analysis source.

Cheaper exports that need no re-encode (a still frame with the full path, a vector path image, the
rep-metrics sidecar of ADR-0012 Section 7) are candidates for a later decision and are not specified
here. Data export (JSON, CSV) is an adapter around the `Analysis` and is not changed.

## 10. Latency requirements (non-numeric)

The overlay is a pure function of a stored `Analysis` and a verified binding. It must show the path
as soon as the Analysis exists, without waiting for any export, and without re-decoding for the path:

- path geometry is computed once per `(Analysis, content rect)` and cached;
- the per-frame step is a lookup by frame index, not a scan or a tracker call;
- binding verification reads timestamps and does not decode pixels.

Numeric budgets, hardware and thermal evidence belong to #179 and need an owner decision. Nothing
here is a latency claim.

## 11. Reference cases and checks

The executable expectations are in
[`validation/reference-cases/video-overlay-v1.json`](../../validation/reference-cases/video-overlay-v1.json),
validated by [`video-overlay-reference-cases-v1.schema.json`](../../validation/schema/video-overlay-reference-cases-v1.schema.json)
and `python validation/tools/schema_check.py`. Screen positions, path and navigation data are dyadic,
and the timeline cases compare integer microseconds and frame indices, so each expected value is
exactly reproducible by any binary64 consumer (Rust, Python, the future app). Some timeline
timestamps in seconds (for example 1/90000-tick values) are not dyadic; they are compared only after
rounding to integer microseconds.

| Group | Cases | What they pin |
| --- | --- | --- |
| Transform | exact-fit half-scale; landscape in portrait view (letterbox); portrait in landscape view (pillarbox); 90°, 180° and 270° coded rasters; portrait-native; decoder-downscaled surface; zoom and pan | S0→S1→S3 positions, the `+ 0.5` pixel-centre offset, the frame corners, exact inverse |
| Transform refusals | non-square pixels; transposed player raster; padded decoder raster; wrong applied rotation with matching dimensions; raster tolerance at 1 and 2 px; absent source hash; rotation not reportable (refused, and accepted with a recorded conformance pass) | Section 2 checks |
| Timeline | non-zero start with a rebased player and with an absolute-PTS player; irregular (VFR) timestamps; a combined rotated, portrait, VFR, non-zero-start clip; trimmed analysis range; lost frame; seek landing on an earlier keyframe; frame-exact seek failure | Section 3, including where `round(pos · fps)` picks a different frame |
| Binding refusals | one frame 2 ms off; 2 µs disagreement; observation without `frame_index`; player cannot list timestamps; frame-count disagreement; `frame_index` beyond the player's frames | `timeline_mismatch`, `timeline_unavailable` |
| Path | lost breaks the path; low-confidence styling; single-point run; frame-index hole; leading and trailing loss; trail cut at the displayed frame | Section 4 |
| Navigation | rep seek targets and highlights derived from [`rep-metrics-v1.json`](../../validation/reference-cases/rep-metrics-v1.json) cases (three reps with an incomplete one; withheld rep with a gap; no rep and only a rejected run) | Section 6 and the link to #180 |

| Check | Command | What it proves |
| --- | --- | --- |
| Case consistency | `python -m unittest discover -v -s validation/tests -p 'test_video_overlay_reference.py'` | the expected values follow this contract, and the navigation cases follow `rep-metrics-v1.json` |
| Schema | `python validation/tools/schema_check.py` | the case file is well-formed and catalogued |

Tolerances a future implementation is held to: screen positions equal exactly on these dyadic cases
(real clips: within `1e-6` view pixels, f64); frame indices, states, segments, refusal reasons and
seek targets exact; timeline binding tolerance 1 µs; raster tolerance 1 player pixel. The two proposed
tolerances are engineering choices without evidence behind them and are re-examined, not asserted.

### Existing evidence this reuses

The diagnostic renderer ([`DIAGNOSTIC_RENDERING.md`](DIAGNOSTIC_RENDERING.md)) is a canonical consumer
and is not changed. Its tests pin the same invariants on the Rust side:

| Invariant | Existing test |
| --- | --- |
| a coordinate `x` is drawn at `x + 0.5` | `overlays_place_integer_coordinates_at_drawn_pixel_centres` (`diagnostic_svg.rs`) |
| a lost sample breaks the path | `loss_breaks_raw_trajectory`, `metric_segments_use_canonical_timestamps_to_preserve_loss_gaps` |
| rotation metadata is not re-applied to measurements | `renderer_preserves_nonzero_orientation_metadata_without_reorienting_measurements` |
| loss and absent layers stay explicit | `failure_report_keeps_gaps_and_missing_layers_explicit` |
| rotated VFR footage, 90/180/270 pixel-exact rotation | `parses_rotated_vfr_stream_like_real_phone_footage`, `display_rotation_metadata_turns_frames_counter_clockwise` |
| timestamps are relative to `start_pts`, with a non-1/fps time base | `timestamps_are_relative_to_stream_start_pts` |
| non-square pixels are rejected | `rejects_non_square_pixels_but_accepts_unknown_ratio` |

Not covered by any existing test: a real file with a non-zero `start_pts` or irregular PTS through the
full decode, container crop, a portrait-native recording, and every behaviour of a mobile player. The
reference cases cover these as synthetic geometry and time only. The player-side checks in Sections 2
and 3 are acceptance criteria for the future implementation, to be exercised on real devices.

## 12. Not decided here

- The player library, decoder, binding and UI toolkit. That needs current official documentation and a
  dependency review in the implementing task.
- Whether the first release shows a filtered path as an optional layer.
- The raster tolerance (1 px) and the timeline tolerance (1 µs). Both are proposals.
- Whether a rep selection seeks to the metric interval start (this contract) or the window's first
  frame.
- Whether and when annotated video export is built.
- Numeric latency budgets (#179), supported lifts, labels and the displayed mean (ADR-0012).
- Anything about `analysis-v1`, #79, #57 or M1 entry.
