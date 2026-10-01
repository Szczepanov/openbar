# ADR-0006: M0 video decode via external FFmpeg processes

- Status: Proposed
- Date: 2026-10-01

## Context

ADR-0005 kept tracker experiments decoder-agnostic: trackers consume `FrameSample` values
(display-oriented 8-bit grayscale image, caller-supplied authoritative timestamp, optional frame
index). Every tracker run so far has used procedurally generated frames. Issue #7 records that
neither candidate can be selected until real decoded-video fixtures exist, and #12 (`analyze`)
needs a coherent decode approach.

The decode layer must satisfy contracts that already exist:

- timestamps are decoder presentation timestamps (PTS) in seconds from media start, never
  `frame_index / nominal_fps` (`docs/validation/ANNOTATION.md`, `MANUAL_TARGET_SEED.md`);
- pixel coordinates are display-oriented **after** rotation metadata is applied;
- variable-frame-rate phone footage must work;
- identical inputs and pipeline version produce identical measurement output;
- dependencies record source, licence, purpose, distribution compatibility and native
  implications (`docs/architecture/ARCHITECTURE.md`, "Dependency policy").

Typical M0 inputs are self-recorded phone clips (H.264/HEVC in MP4/MOV, 10–90 MB, often VFR,
often with 90° rotation metadata, sometimes 10-bit HDR).

### Options considered

| Option | Codec coverage | Licence exposure | Build impact | Notes |
| --- | --- | --- | --- | --- |
| A. Spawn `ffprobe`/`ffmpeg` executables | Everything FFmpeg supports | None in the OpenBar binary; FFmpeg runs as a separate program | No new Cargo dependency | Requires FFmpeg on `PATH`; output may vary across FFmpeg builds |
| B. Link libav* (`ffmpeg-next` / `ffmpeg-sys`) | Same | LGPL/GPL obligations depend on the FFmpeg build linked or shipped | Native toolchain + headers on Windows/CI | Tightest integration, highest setup cost |
| C. OpenCV `VideoCapture` | Via its FFmpeg backend | Apache-2.0 plus the bundled FFmpeg | Large native dependency | Exposes PTS poorly; rotation handling varies by version |
| D. Pure-Rust demux + decoder (e.g. `mp4` + `openh264`) | H.264 only; no HEVC | BSD-2 (openh264 has Cisco binary-licence nuances) | Moderate | Most iPhone footage is HEVC, so it cannot read typical inputs |
| E. Platform APIs (Media Foundation / AVFoundation / MediaCodec) | Platform-dependent | OS licences | Per-platform code | Right for the future app, wrong for a cross-platform M0 harness |

Process-wrapper crates such as `rust_ffmpeg` 1.0 were also considered as a variant of option
A. They spawn the same executables but target file-to-file transcoding through an async command
builder, with no per-frame PTS or raw-frame access. They would replace only the command
construction and would add tokio, regex, thiserror, tracing, derive_builder and tempfile. That
conflicts with the dependency policy and the hand-written error convention.

## Decision

For M0, decode in `apps/openbar-cli` by spawning external `ffprobe` and `ffmpeg` processes
(option A). No Cargo dependency is added, and FFmpeg is neither linked nor redistributed.

### Frame-source procedure

1. **Probe.** `ffprobe -select_streams v:0` returns stream metadata (codec, pixel format,
   colour range, coded size, `time_base`, `start_pts`, display-matrix rotation, colour transfer)
   and the per-frame integer `pts` list for the first *video* stream. Its stream index is not
   assumed: phone files may store audio as stream 0. A sample aspect ratio other than 1:1 (or
   FFmpeg's "unknown" 0:1) is rejected, because a circular plate would no longer be circular in
   decoded pixels.
2. **Decode.** `ffmpeg -nostdin -hide_banner -v error -autorotate -i <input> -map 0:v:0
   -fps_mode passthrough -enc_time_base demux -pix_fmt gray -f rawvideo -` streams
   display-oriented 8-bit luma frames to stdout. `-autorotate` is FFmpeg's default but is passed
   explicitly because orientation is part of this contract. Passthrough mode forbids frame duplication and
   dropping. `-enc_time_base demux` keeps the source time base. Without it, closely spaced VFR
   frames collide after rounding to the nominal rate and FFmpeg emits non-monotonic DTS warnings.
   A non-zero exit fails decoding. Remaining stderr lines are kept as diagnostics, not
   discarded.
3. **Pair by decode order.** Frame *i* from stdout gets probed timestamp *i*. Expected
   display dimensions are derived from coded size and rotation, and the byte count must be an
   exact multiple of `width_px × height_px`. If the frame count differs from the probe count,
   decoding fails closed. Nothing is resynchronized or padded. The byte count cannot detect an
   orientation error, because W×H and H×W frames are the same size. Orientation correctness
   therefore rests on the rotation contract below and a dedicated rotated-fixture test.
4. **Rotation contract.** The rotation recorded is the display-matrix rotation reported by
   ffprobe. FFmpeg's convention is counter-clockwise degrees to apply before display, and the
   value is normalised to {0, 90, 180, 270}. Any other angle is rejected as unsupported. The
   seed and annotation field `source_rotation_deg` uses this same convention. FFprobe reports
   180° as −180 and 270° as −90, so values are taken modulo 360. A test generates 90/180/270
   copies of the synthetic fixture with `-display_rotation` and checks every pixel: a reported
   rotation of 90 yields the coded frame turned 90° counter-clockwise. Fixture manifests keep
   recording the encoded raster size, which is swapped for 90/270, as `ANNOTATION.md` defines.
5. **Timestamps.** `timestamp_s = (pts − start_pts) × time_base`. The difference is computed in
   integer ticks and only then converted to `f64`, so media start is the video stream's
   `start_pts`. Timestamps must be finite and strictly increasing. A frame without a PTS is a
   decode error. Its timestamp is never synthesized from frame rate.
6. **Range selection.** An optional `[start_s, end_s]` window is applied in Rust to the probed
   timestamps while frames stream by. FFmpeg seek/trim options are not used because they can
   re-base timestamps or use boundary semantics that differ from ours.
7. **Memory guard.** Trackers currently take `&[FrameSample]`, so selected frames are held in
   memory. The CLI enforces an explicit byte budget (frames × width × height). When the budget
   is exceeded, decoding fails with a message asking for a narrower time range. It never
   downsamples silently. For scale, a 44.7 s 720p clip is 1.24 GB of grayscale frames.
8. **No implicit resampling or scaling.** Frames stay at native display resolution, because
   annotations and seeds are expressed in native display pixels. Any future downscale must be
   explicit, versioned and recorded, with calibration scaled to match.

### Process handling

- **stderr.** ffmpeg's stderr is drained as bytes on a separate thread, so a non-UTF-8
  message can neither block the pipe nor be lost.
- **Stopping ffmpeg.** The decoder is killed only when reading fails while frames are still
  flowing. Count and truncation errors are detected at end of stream and wait for ffmpeg to
  exit normally.
- **Error precedence.** If ffmpeg itself exits unsuccessfully, that failure takes precedence
  over any downstream symptom.
- **Diagnostics.** Recorded diagnostics are normalised before they reach output: per-run
  context pointers (`@ 0x…`) are stripped and the input path becomes `<input>`.

### Provenance recorded in analysis/benchmark output

The output records decoder identity (`ffmpeg -version` first line), the exact argument vector,
source SHA-256, codec, source pixel format, colour range, colour transfer, coded and display dimensions,
rotation, `time_base`, `start_pts`, decoded frame count and the selected time range.

### Grayscale semantics

`-pix_fmt gray` produces the luma plane converted to full-range 8-bit. Limited-range (TV)
sources are expanded, so codes 16–235 map to 0–255, and values outside that range clip. Full-range
sources pass through unchanged. The decoder's frame-level range decides, not the container's
stream tag. One real clip was tagged `yuvj420p` with `color_range=tv`, and its output matched
the native full-range Y plane bit for bit. No colour matrix, white balance or tone mapping is
applied. For HDR (HLG/PQ) sources, luma is in the source
transfer function rather than SDR. These clips are accepted and tagged via the recorded colour
transfer, and their tracking quality is evaluated as a condition rather than assumed.

## Evidence from real footage

Before implementation, the procedure was checked by hand with FFmpeg 9.0.2 (gyan.dev full
build, Windows) on two private self-recorded Android clips. Both are H.264 in MP4, with no
B-frames:

| Check | Clip A | Clip B |
| --- | --- | --- |
| Coded → display size | 720×1280 → 720×1280 | 1280×720 → 720×1280 |
| Rotation metadata | none (encoded portrait) | display matrix, `rotation=90`; decoded upright (visually confirmed) |
| Video stream index | 1 (audio is 0) | 0 |
| Pixel format / range tag | `yuv420p` / tv | `yuvj420p` / tv (conflicting tags) |
| Frames: probe vs decode | 320 = 320 | 1348 = 1348 |
| Frame spacing | constant 33.33 ms (CFR) | 138 distinct deltas, 27.98–38.76 ms (VFR) |
| Drift if `index / 30` were used | none | 0.19 s by the last frame |
| Two decodes byte-identical | yes | yes |
| Decode time / clip duration | 2.8 s / 10.7 s | 15.5 s / 44.7 s |
| Grayscale frame memory | 0.29 GB | 1.24 GB |

Not yet exercised on real footage: HEVC, B-frame reordering, 10-bit/HDR, non-zero
`start_pts`, and resolutions above 720p. 180°/270° rotation is covered only by generated copies
of the synthetic fixture. Each needs at least one real or generated clip before
this ADR is accepted.

## Consequences

- Real phone footage (HEVC, VFR, rotated) can reach the existing trackers without a new
  Cargo dependency or native build step, which unblocks real-data runs for #7, #10, #11 and #14.
- FFmpeg becomes a documented runtime prerequisite for decode-dependent CLI commands. CI must
  install it before running decode integration tests. Tests that need decoding skip only with an
  explicit, visible reason, never silently.
- Byte-identical frames are guaranteed only for the same FFmpeg build. Recording the decoder
  version keeps cross-build differences diagnosable rather than hidden.
- Pairing by decode order depends on passthrough mode. The count check turns any violation into
  an error instead of misaligned timestamps.
- Holding frames in memory limits clip length. A 45 s 720p phone clip already needs 1.24 GB, and
  1080p would need about 2.8 GB, so range selection is required from the start. If real clips
  routinely exceed the budget even with a range, a streaming tracker interface is the follow-up. That changes the ADR-0005 boundary
  and needs its own decision.
- This is an M0 harness decision. The future app may use platform decoders (option E) behind the
  same frame boundary, provided it honours the same timestamp and orientation contract.

## Licence note

FFmpeg is LGPL-2.1+ or GPL-2.0+ depending on build configuration. OpenBar invokes it as a
separate executable via its documented command-line interface and neither links nor ships it, so
FFmpeg's licence does not attach to OpenBar's source under PolyForm Shield 1.0.0. Bundling FFmpeg
binaries with any future distribution is a separate decision requiring licence review under
ADR-0002.

## Revisit when

- process-spawn or pipe overhead makes offline processing slower than video duration on
  reference hardware (an M0 gate in #14);
- a frame-accurate PTS mismatch between probe and decode is observed on real footage;
- the product app needs decoding (option E) or bundled decoding.
